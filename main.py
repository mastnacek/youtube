import sys
import re
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "yt-dlp"))

import yt_dlp
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich import box

console = Console()

DOWNLOAD_DIR = Path.home() / "Downloads" / "YouTube"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TEMP_DIR = Path("/tmp/yt-dlp-work")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class SilentLogger:
    """Potlačí přímý výpis yt-dlp — vše jde přes rich."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


# Windows + Linux zakázané znaky ve jménech souborů/složek
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*&\x00-\x1f]')
_URL_RE = re.compile(r'[\(\[]?(?:https?://|www\.)\S+[\)\]]?', re.IGNORECASE)
# Windows rezervovaná jména (case-insensitive)
_WIN_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE
)


def sanitize_name(name: str) -> str:
    """NFD dekompozice → ASCII (diacritika→base), drop emoji, URL a unsafe znaky (Windows+Linux)."""
    name = _URL_RE.sub("", name)
    name = unicodedata.normalize("NFD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = _UNSAFE_CHARS.sub("", name)
    name = re.sub(r"[\s_]+", " ", name).strip(" .")
    if _WIN_RESERVED.match(name):
        name = f"_{name}"
    return name or "unknown"


def rename_downloaded(new_files: set[Path], is_playlist: bool) -> list[Path]:
    """Sanitizuje a sekvenčně přečísluje nové soubory (bez mezer v číslování)."""
    sorted_files = sorted(new_files)
    result = []

    for i, path in enumerate(sorted_files, 1):
        stem = path.stem
        # Odstraň případný yt-dlp prefix (00001-Název nebo 1-Název)
        stem = re.sub(r"^\d+[-_.\s]+", "", stem)
        clean = sanitize_name(stem)

        new_name = f"{i:02d} - {clean}{path.suffix}" if is_playlist else f"{clean}{path.suffix}"
        new_path = path.parent / new_name

        # Kolidující název → přidej suffix
        if new_path.exists() and path != new_path:
            j = 2
            while new_path.exists():
                new_path = path.parent / f"{new_path.stem} ({j}){path.suffix}"
                j += 1

        if path != new_path:
            path.rename(new_path)
        result.append(new_path)

    return result


def print_banner():
    console.print(Panel.fit(
        "[bold yellow]YouTube Downloader[/bold yellow]\n"
        "[dim]Powered by yt-dlp + rich[/dim]",
        border_style="yellow",
        padding=(1, 4),
    ))


def format_size(bytes_val):
    if bytes_val is None:
        return "?"
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


def format_duration(seconds):
    if seconds is None:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def show_info(info: dict):
    is_playlist = info.get("_type") == "playlist"

    if is_playlist:
        entries = [e for e in (info.get("entries") or []) if e is not None]
        table = Table(
            title=f"[bold]Playlist: {info.get('title', 'Neznámý')}[/bold]",
            box=box.ROUNDED, border_style="cyan", show_lines=True,
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Název", style="white")
        table.add_column("Délka", style="green", justify="right")
        for i, entry in enumerate(entries, 1):
            table.add_row(str(i), entry.get("title", "Neznámé"), format_duration(entry.get("duration")))
        console.print(table)
        console.print(f"[dim]Celkem {len(entries)} videí | Uložit do: {DOWNLOAD_DIR}[/dim]")
    else:
        table = Table(box=box.ROUNDED, border_style="cyan", show_header=False)
        table.add_column("Klíč", style="bold cyan", width=14)
        table.add_column("Hodnota", style="white")
        table.add_row("Název", info.get("title", "?"))
        table.add_row("Kanál", info.get("uploader", "?"))
        table.add_row("Délka", format_duration(info.get("duration")))
        views = info.get("view_count")
        table.add_row("Zhlédnutí", f"{views:,}" if views else "?")
        table.add_row("Uložit do", str(DOWNLOAD_DIR))
        console.print(table)


class ProgressTracker:
    def __init__(self, progress: Progress, task_id):
        self._progress = progress
        self._task = task_id

    def hook(self, d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed")
            eta = d.get("eta")

            speed_str = f"{format_size(speed)}/s" if speed else "?"
            eta_str = f"{eta}s" if eta else "?"
            title = d.get("info_dict", {}).get("title", "")
            title_short = (title[:48] + "…") if len(title) > 48 else title

            pct = (downloaded / total * 100) if total else 0
            self._progress.update(
                self._task, completed=pct,
                description=f"[cyan]{title_short}[/cyan] [dim]{speed_str} | eta {eta_str}[/dim]",
            )
        elif d["status"] == "finished":
            self._progress.update(self._task, completed=100, description="[green]Zpracovávám...[/green]")


def build_opts(mode: str, is_playlist: bool, tracker: ProgressTracker, out_dir: Path) -> dict:
    # Playlist: velký numerický prefix pro správné řazení po stažení
    filename = "%(playlist_index)05d-%(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s"

    base = {
        "outtmpl": str(out_dir / filename),
        "paths": {"temp": str(TEMP_DIR)},
        "progress_hooks": [tracker.hook],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "ignoreerrors": True,       # přeskočí nedostupná videa v playlistu
        "logger": SilentLogger(),
    }

    if mode == "audio":
        return {
            **base,
            "format": "bestaudio/best",   # opus/webm = nejvyšší kvalita na YT
            "writethumbnail": True,
            "postprocessors": [
                # 1) opus/webm → mp3 192 kbps
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                # 2) YT thumbnail je webp — EmbedThumbnail potřebuje jpg
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
                # 3) ID3 metadata
                {"key": "FFmpegMetadata", "add_metadata": True},
                # 4) cover art do MP3
                {"key": "EmbedThumbnail"},
            ],
        }

    return {
        **base,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
    }


def resolve_output_dir(info: dict) -> Path:
    """Playlist → vlastní složka se sanitizovaným názvem. Single video → DOWNLOAD_DIR."""
    if info.get("_type") == "playlist":
        title = info.get("title") or "playlist"
        folder = sanitize_name(title)
        out = DOWNLOAD_DIR / folder
        out.mkdir(parents=True, exist_ok=True)
        return out
    return DOWNLOAD_DIR


def download(url: str, mode: str, info: dict) -> bool:
    is_playlist = info.get("_type") == "playlist"
    out_dir = resolve_output_dir(info)
    before = set(out_dir.glob("*"))
    error_msg: str | None = None

    with Progress(
        SpinnerColumn(),
        BarColumn(bar_width=30),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("Připravuji...", total=100)
        tracker = ProgressTracker(progress, task_id)
        opts = build_opts(mode, is_playlist, tracker, out_dir)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            error_msg = strip_ansi(str(e)).strip()

    if error_msg:
        console.print(f"[red]Chyba stahování:[/red] {error_msg}")
        return False

    new_files = set(out_dir.glob("*")) - before
    if not new_files:
        console.print("[yellow]Žádné soubory nebyly staženy.[/yellow]")
        return False

    renamed = rename_downloaded(new_files, is_playlist)
    console.print(f"[bold green]✓ Staženo {len(renamed)} soubor(ů) do:[/bold green] {out_dir}\n")
    return True


def fetch_info(url: str) -> dict | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "logger": SilentLogger(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        console.print(f"[red]Nelze načíst info:[/red] {strip_ansi(str(e)).strip()}")
        return None


def choose_mode() -> str:
    console.print("\n[bold]Co chceš stáhnout?[/bold]")
    console.print("  [cyan]1[/cyan]  Audio → MP3 192 kbps (s cover artem)")
    console.print("  [cyan]2[/cyan]  Video → MP4 (nejlepší kvalita)\n")
    choice = Prompt.ask("Volba", choices=["1", "2"], default="1")
    return "audio" if choice == "1" else "video"


def main():
    print_banner()

    while True:
        console.print()
        console.print(Rule("[dim]Nové stahování[/dim]"))

        url = Prompt.ask("\n[bold yellow]URL[/bold yellow]  (video nebo playlist, [dim]q[/dim] = konec)")
        if url.strip().lower() in ("q", "quit", "exit", ""):
            console.print("\n[dim]Nashledanou, mistře Jardo![/dim]")
            break

        url = url.strip()

        console.print("\n[dim]Načítám informace...[/dim]")
        info = fetch_info(url)
        if info is None:
            continue

        is_playlist = info.get("_type") == "playlist"
        console.print()
        show_info(info)

        mode = choose_mode()
        label = "audio MP3 192 kbps + cover" if mode == "audio" else "video MP4"

        if not Confirm.ask(f"\n[bold]Stáhnout jako {label}?[/bold]", default=True):
            console.print("[dim]Zrušeno.[/dim]")
            continue

        console.print()
        download(url, mode, info)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[dim]Přerušeno.[/dim]")
