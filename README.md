# YouTube Downloader

TUI aplikace pro stahování videí a playlistů z YouTube. Audio převádí na **MP3 192 kbps**
s vloženým cover artem, video stahuje v nejlepší kvalitě jako **MP4**. Postavená na
[yt-dlp](https://github.com/yt-dlp/yt-dlp) a [rich](https://github.com/Textualize/rich).

## Funkce

- **Audio → MP3 192 kbps** — ID3 metadata + cover art z videa
- **Video → MP4** — nejlepší dostupná kvalita (merge přes ffmpeg)
- **Playlisty** — vlastní složka dle názvu, soubory očíslované `01 - Název.mp3`, …
- **`.m3u` soubor** — po stažení playlistu se automaticky vytvoří přehrávací seznam
- **Sanitizace názvů** — whitelist `[a-zA-Z0-9 -]`, bez diakritiky, bez zakázaných
  znaků Windows; volba `2` v menu umí dodatečně vyčistit i existující složku

## Požadavky

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (nebo pip)
- **ffmpeg** v `PATH`
- **deno** v `PATH` — řeší JS challenge YouTube (n-challenge)
- **bgutil PO token provider** — klon a build:

  ```shell
  git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git ~/bgutil-ytdlp-pot-provider
  cd ~/bgutil-ytdlp-pot-provider/server
  npm ci && npx tsc
  ```

  Aplikace ho volá v script módu (žádný běžící server není potřeba).

## Instalace

```shell
uv sync
```

## Spuštění

```shell
uv run python main.py
```

Stáhnuté soubory ukládá do `~/Downloads/YouTube/` — playlist do vlastní podsložky.

## Proč je yt-dlp připnutý k forku?

YouTube od léta 2026 vynucuje **SABR streaming**: klasické (https) odkazy
`googlevideo.com` servírují jen prvních ~1 MiB streamu a větší rozsah vrací
`HTTP 403` — nezávisle na klientovi, UA nebo PO tokenu. Stahování celých souborů
proto aplikace řeší přes **HLS (m3u8) formáty**, které chodí po segmentech.

Podpora HLS ze `visionos` klienta + SABR protokol je v
[PR #13515](https://github.com/yt-dlp/yt-dlp/pull/13515), které ještě není
ve stable release — proto `pyproject.toml` odkazuje na větev tohoto PR.
Jakmile bude sloučen do release, stačí v `pyproject.toml` vrátit běžný
version spec (`yt-dlp>=YYYY.M.D`).

## Struktura

```text
main.py          # celá aplikace (TUI, stahování, sanitzace, .m3u)
pyproject.toml   # závislosti (uv)
uv.lock          # zamčené verze
```
