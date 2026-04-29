import sys
import re
from pathlib import Path

# yt-dlp je lokalni zdrojak, pridame do path
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

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class SilentLogger:
    """Potlači přímý výpis yt-dlp — vše jde přes rich."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


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
    """Zobraz info o videu nebo playlistu."""
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
            table.add_row(
                str(i),
                entry.get("title", "Neznámé"),
                format_duration(entry.get("duration")),
            )

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
                self._task,
                completed=pct,
                description=f"[cyan]{title_short}[/cyan] [dim]{speed_str} | eta {eta_str}[/dim]",
            )
        elif d["status"] == "finished":
            self._progress.update(
                self._task, completed=100,
                description="[green]Zpracovávám...[/green]",
            )


def build_opts(url: str, mode: str, tracker: ProgressTracker) -> dict:
    is_playlist = "list=" in url or "playlist" in url
    outtmpl = str(DOWNLOAD_DIR / ("%(playlist_index)s-%(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s"))

    base = {
        "outtmpl": outtmpl,
        "progress_hooks": [tracker.hook],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "logger": SilentLogger(),
    }

    if mode == "audio":
        return {
            **base,
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail"},
            ],
        }

    return {
        **base,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
    }


def download(url: str, mode: str) -> bool:
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
        opts = build_opts(url, mode, tracker)

        error_msg: str | None = None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            error_msg = strip_ansi(str(e)).strip()

    if error_msg:
        console.print(f"[red]Chyba stahování:[/red] {error_msg}")
        return False

    console.print(f"[bold green]✓ Staženo do:[/bold green] {DOWNLOAD_DIR}\n")
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

        console.print()
        show_info(info)

        mode = choose_mode()
        label = "audio MP3 192 kbps + cover" if mode == "audio" else "video MP4"

        if not Confirm.ask(f"\n[bold]Stáhnout jako {label}?[/bold]", default=True):
            console.print("[dim]Zrušeno.[/dim]")
            continue

        console.print()
        download(url, mode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[dim]Přerušeno.[/dim]")
