#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mlx-whisper>=0.4; sys_platform == 'darwin' and platform_machine == 'arm64'"]
# ///
"""Print the full transcript of an online video as clean, readable text.

Usage:
    uv run video/transcript.py <url> [--lang it,en] [-o FILE] [--timestamps]
        [--model REPO] [--no-fallback] [--cookies-from-browser BROWSER | --cookies FILE]

Works with any site yt-dlp (Homebrew binary) supports: YouTube, Instagram, TikTok, X,
Facebook, Vimeo, Reddit... Subtitles are read without downloading the video: manual subtitles
first, then automatic captions in the video's original language. When a video has none, only
the audio is downloaded and transcribed locally with mlx-whisper (Apple Silicon only; the
model is downloaded on the first run). No login is used unless --cookies-from-browser or
--cookies is given. One video at a time: posts with several videos are rejected. Temporary files live in a
temporary directory that is always removed. Output messages are in Italian.
"""

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_LANGS = ("it", "en")
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_YTDLP = 3

# paragraph shaping, in words
PARAGRAPH_MIN = 50  # a sentence end closes the paragraph after this many words
PARAGRAPH_MAX = 120  # forced break, for automatic captions without punctuation
PAUSE_MIN = 20  # a pause closes the paragraph after this many words
PAUSE_SECONDS = 4.0
SENTENCE_END = ".?!…"

SOURCE_LABELS = {
    "manual": "sottotitoli manuali",
    "auto": "sottotitoli automatici",
    "local": "trascrizione locale",
}

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# yt-dlp phrasings of "log in to see this", across sites
LOGIN_HINTS = (
    "sign in", "login", "log in", "logged-in", "authentication", "members-only",
    "confirm your age", "--cookies",
)
# Checked live on 2026-11-03: some Instagram reels list only video formats with or without a
# login, so suggesting one would not help.
NO_AUDIO = (
    "nessun sottotitolo e il sito fornisce solo il video senza audio (succede con alcuni reel di "
    "Instagram, anche con il login): non c'è niente da trascrivere"
)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


class TranscriptError(Exception):
    def __init__(self, message, code=EXIT_FAILED):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Source:
    kind: str  # "manual" | "auto"
    lang: str


# --- pure functions -------------------------------------------------------------------------


def normalize_url(url):
    """Check a video URL; YouTube URLs become the canonical watch URL (playlist dropped).

    Any other http(s) URL is passed to yt-dlp as it is: yt-dlp knows which sites it supports.
    """
    url = url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or "." not in host:
        raise TranscriptError(f"URL non valido: {url!r} (serve l'indirizzo http(s) di un video)", EXIT_USAGE)
    if host not in YOUTUBE_HOSTS and host not in ("youtu.be", "www.youtu.be"):
        return url
    video_id = None
    if host in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    else:
        parts = parsed.path.strip("/").split("/")
        if parts[0] == "watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parts[0] in ("shorts", "live", "embed") and len(parts) > 1:
            video_id = parts[1]
    if not video_id or not VIDEO_ID.match(video_id):
        raise TranscriptError(
            f"URL non valido: {url!r} (serve l'indirizzo di un singolo video YouTube)",
            EXIT_USAGE,
        )
    return f"https://www.youtube.com/watch?v={video_id}"


def check_single_video(info):
    """Reject posts with several videos (carousels, threads): one video at a time."""
    if info.get("_type") in ("playlist", "multi_video"):
        count = len(info.get("entries") or [])
        raise TranscriptError(
            f"il link contiene {count or 'più'} video (carosello, thread o playlist): "
            "per ora si trascrive un video alla volta"
        )


def has_audio(info):
    """False only when yt-dlp lists formats and every one of them is video only.

    Instagram without login serves video-only files: there is nothing to transcribe.
    """
    formats = info.get("formats")
    if not formats:
        return True  # nothing listed: let the download decide
    return any(f.get("acodec") != "none" for f in formats)


def last_line(text):
    """Last non-empty line of a multi-line message (ffmpeg prints its whole banner)."""
    return next((line.strip() for line in reversed(str(text).splitlines()) if line.strip()), "")


def _matches(key, lang):
    return key == lang or key.startswith(lang + "-")


def choose_source(info, langs):
    """Pick the subtitles to use from yt-dlp's info JSON, or None for the local fallback.

    Order: manual subtitles in the preferred languages, manual subtitles in the video's
    original language, automatic captions in the original language. Automatic captions in
    other languages are machine translations and are never used.
    """
    manual = [k for k in (info.get("subtitles") or {}) if k != "live_chat"]
    auto = list(info.get("automatic_captions") or {})
    original = info.get("language")
    if not original:
        orig_keys = [k for k in auto if k.endswith("-orig")]
        original = orig_keys[0][: -len("-orig")] if orig_keys else None

    for lang in langs:
        for key in manual:
            if _matches(key, lang):
                return Source("manual", key)
    if original:
        for key in manual:
            if _matches(key, original):
                return Source("manual", key)
        auto_original = [k for k in auto if _matches(k, original)]
        if auto_original:
            # "xx-orig" is the raw speech recognition: prefer it to its re-encoded copies
            orig = [k for k in auto_original if k.endswith("-orig")]
            return Source("auto", (orig or auto_original)[0])
    return None


def _seconds(stamp):
    parts = stamp.replace(",", ".").split(":")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def clean_text(text):
    """Strip tags, entities and sound annotations like [Musica] from a subtitle line."""
    text = re.sub(r"<[^>]*>", "", text)
    text = html.unescape(text).replace(" ", " ")
    text = re.sub(r"\[[^\]]*\]|♪+", " ", text)
    return " ".join(text.split())


def parse_vtt(text):
    """Return the cues of a WebVTT file as (start seconds, cleaned text), empty cues dropped."""
    cues = []
    # blocks end at truly empty lines: YouTube puts a line holding one space inside its cues
    for block in re.split(r"\n{2,}", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.strip("\n").split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue  # header, NOTE, STYLE or REGION block
        start = _seconds(lines[timing].split("-->")[0].strip())
        cue_text = clean_text(" ".join(lines[timing + 1 :]))
        if cue_text:
            cues.append((start, cue_text))
    return cues


def merge_cues(cues, rolling):
    """Turn cues into a list of (word, start seconds) without repetitions.

    Automatic captions (rolling=True) repeat the previous line at the top of every cue: the
    longest overlap between the words already emitted and the start of the cue is dropped.
    Manual subtitles only lose cues identical to the previous one.
    """
    words = []
    previous = None
    for start, text in cues:
        cue_words = text.split()
        if rolling:
            overlap = 0
            for k in range(min(len(words), len(cue_words)), 0, -1):
                if [w for w, _ in words[-k:]] == cue_words[:k]:
                    overlap = k
                    break
            cue_words = cue_words[overlap:]
        elif text == previous:
            continue
        previous = text
        words.extend((word, start) for word in cue_words)
    return words


def format_stamp(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"[{hours}:{minutes:02d}:{secs:02d}]" if hours else f"[{minutes:02d}:{secs:02d}]"


def to_paragraphs(words, timestamps=False):
    """Group (word, start) pairs into paragraphs separated by a blank line.

    Punctuated text breaks only at a sentence end (or at twice PARAGRAPH_MAX words, as a
    safety net); text without punctuation breaks at pauses and every PARAGRAPH_MAX words.
    """
    punctuated = any(word[-1] in SENTENCE_END for word, _ in words)
    limit = PARAGRAPH_MAX * 2 if punctuated else PARAGRAPH_MAX
    paragraphs = []
    current = []
    for index, (word, start) in enumerate(words):
        current.append((word, start))
        following = words[index + 1][1] if index + 1 < len(words) else None
        pause = following is not None and following - start >= PAUSE_SECONDS
        boundary = word[-1] in SENTENCE_END or not punctuated
        if (
            (len(current) >= PARAGRAPH_MIN and word[-1] in SENTENCE_END)
            or (pause and boundary and len(current) >= PAUSE_MIN)
            or len(current) >= limit
        ):
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    lines = []
    for paragraph in paragraphs:
        text = " ".join(word for word, _ in paragraph)
        lines.append(f"{format_stamp(paragraph[0][1])} {text}" if timestamps else text)
    return "\n\n".join(lines)


def format_header(info, url, source_label):
    return "\n".join(
        [
            f"Titolo: {info.get('title') or '?'}",
            f"Autore: {info.get('channel') or info.get('uploader') or '?'}",
            f"Piattaforma: {info.get('extractor_key') or info.get('extractor') or '?'}",
            f"URL: {info.get('webpage_url') or url}",
            f"Fonte: {source_label}",
        ]
    )


def ytdlp_error_message(stderr):
    lowered = stderr.lower()
    if "cookies database" in lowered:
        return (
            "non trovo i cookie di quel browser: controlla il nome in --cookies-from-browser e "
            "che sia il browser in cui hai fatto l'accesso al sito"
        )
    if "netscape format" in lowered:
        return (
            "il file di --cookies non è un file di cookie valido: esportalo in formato Netscape "
            "(cookies.txt) dal browser in cui hai fatto l'accesso"
        )
    if "operation not permitted" in lowered and "cookies" in lowered:
        return (
            "macOS non permette di leggere i cookie di Safari: dai l'accesso completo al disco "
            "all'app del terminale (Impostazioni di Sistema > Privacy e sicurezza), usa un "
            "altro browser o esporta i cookie in un file per --cookies"
        )
    if "ip address is blocked" in lowered:
        return (
            "il sito blocca questo video per la tua rete o il tuo paese, anche con il login "
            "(succede su TikTok): yt-dlp non può scaricarlo"
        )
    if "private video" in lowered:
        return "il video è privato"
    if any(hint in lowered for hint in LOGIN_HINTS):
        return (
            "il sito chiede l'accesso per questo video (login, età o membri); se lo vedi nel "
            "browser, riprova con --cookies-from-browser e il nome del browser in cui hai "
            "fatto l'accesso (chrome, brave, firefox, safari...) o con --cookies e un file di cookie"
        )
    if "unsupported url" in lowered:
        return "sito o indirizzo non supportato da yt-dlp"
    if "no video could be found" in lowered or "no video formats found" in lowered:
        return "nel link non c'è nessun video"
    if "429" in lowered or "rate-limit" in lowered or "rate limit" in lowered:
        return "il sito sta limitando le richieste: riprova più tardi"
    if "unavailable" in lowered or "not available" in lowered or "removed" in lowered:
        return "il video non è disponibile"
    last = last_line(stderr)
    return (
        f"yt-dlp non è riuscito a leggere il video ({last or 'nessun dettaglio'}); "
        "prova ad aggiornarlo con brew upgrade yt-dlp"
    )


# --- I/O ------------------------------------------------------------------------------------


def run_ytdlp(args):
    """Run yt-dlp and return its stdout; the only place a process is started for it."""
    result = subprocess.run(["yt-dlp", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise TranscriptError(ytdlp_error_message(result.stderr))
    return result.stdout


def fetch_info(url, options=()):
    out = run_ytdlp(["--dump-single-json", "--no-playlist", "--skip-download", *options, url])
    try:
        info = json.loads(out)
    except json.JSONDecodeError as exc:
        raise TranscriptError("risposta di yt-dlp non leggibile") from exc
    check_single_video(info)
    return info


def download_subtitles(url, source, tmp, options=()):
    flag = "--write-subs" if source.kind == "manual" else "--write-auto-subs"
    run_ytdlp(
        [
            "--skip-download", "--no-playlist", flag,
            "--sub-langs", f"^{re.escape(source.lang)}$", "--sub-format", "vtt",
            "-o", str(Path(tmp) / "subs"), *options, url,
        ]
    )
    files = sorted(Path(tmp).glob("subs*.vtt"))
    if not files:
        raise TranscriptError(f"sottotitoli {source.lang!r} annunciati ma non scaricati")
    return files[0].read_text(encoding="utf-8")


def download_audio(url, tmp, options=()):
    out = str(Path(tmp) / "audio.%(ext)s")
    run_ytdlp(["-f", "bestaudio/best", "--no-playlist", "-o", out, *options, url])
    files = [p for p in Path(tmp).glob("audio.*") if not p.name.endswith(".part")]
    if not files:
        raise TranscriptError("audio non scaricato")
    return files[0]


def transcribe_local(audio, model, language):
    """Transcribe with mlx-whisper; returns (start seconds, text) segments."""
    if shutil.which("ffmpeg") is None:
        raise TranscriptError("ffmpeg non trovato: serve per la trascrizione locale (brew install ffmpeg)")
    try:
        import mlx_whisper
    except ImportError as exc:
        raise TranscriptError(
            "mlx-whisper non disponibile: la trascrizione locale funziona solo su Mac con "
            "Apple Silicon, avviando lo script con uv run"
        ) from exc
    try:
        result = mlx_whisper.transcribe(str(audio), path_or_hf_repo=model, language=language)
    except Exception as exc:  # model download, decoding: report, do not crash
        raise TranscriptError(f"trascrizione locale fallita: {last_line(exc)}") from exc
    return [(seg["start"], clean_text(seg["text"])) for seg in result.get("segments", [])]


def login_options(cookies_from_browser=None, cookies=None):
    """yt-dlp options for the login; empty when none was asked for."""
    if cookies_from_browser:
        return ["--cookies-from-browser", cookies_from_browser]
    if cookies:
        return ["--cookies", cookies]
    return []


def transcript(url, langs, timestamps, model, fallback, options=()):
    """Return the full text (header + paragraphs) for a video; `options` holds the login."""
    url = normalize_url(url)
    if shutil.which("yt-dlp") is None:
        raise TranscriptError("yt-dlp non trovato: installalo con brew install yt-dlp", EXIT_NO_YTDLP)
    options = list(options)
    with tempfile.TemporaryDirectory(prefix="video-transcript-") as tmp:
        info = fetch_info(url, options)
        source = choose_source(info, langs)
        if source:
            cues = parse_vtt(download_subtitles(url, source, tmp, options))
            words = merge_cues(cues, rolling=source.kind == "auto")
            label = f"{SOURCE_LABELS[source.kind]} ({source.lang})"
        else:
            if not fallback:
                raise TranscriptError("nessun sottotitolo disponibile e trascrizione locale disattivata")
            if not has_audio(info):
                raise TranscriptError(NO_AUDIO)
            print(f"Nessun sottotitolo: scarico l'audio e lo trascrivo con {model}...", file=sys.stderr)
            segments = transcribe_local(download_audio(url, tmp, options), model, info.get("language"))
            words = merge_cues(segments, rolling=False)
            label = f"{SOURCE_LABELS['local']} ({model})"
        if not words:
            raise TranscriptError("la trascrizione è vuota")
        return f"{format_header(info, url, label)}\n\n{to_paragraphs(words, timestamps)}\n"


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="transcript.py",
        description=(
            "Trascrizione completa di un video online (YouTube, Instagram, TikTok, X, Facebook, "
            "Vimeo, Reddit e gli altri siti di yt-dlp) come testo leggibile."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""esempi:
  uv run video/transcript.py "https://youtu.be/VIDEO_ID" | pbcopy
  uv run video/transcript.py "https://www.instagram.com/reel/ID/"
  uv run video/transcript.py URL --lang en,it -o trascrizione.txt
  uv run video/transcript.py URL --timestamps
  uv run video/transcript.py URL --no-fallback
  uv run video/transcript.py URL --model mlx-community/whisper-small-mlx
  uv run video/transcript.py URL --cookies-from-browser chrome   # solo se il sito chiede il login
  uv run video/transcript.py URL --cookies ~/cookies.txt         # login da un file esportato

fonte: sottotitoli manuali > automatici (lingua originale) > trascrizione locale.
exit code: 0 ok, 1 video non disponibile o trascrizione fallita, 2 argomenti o URL non
validi, 3 yt-dlp assente.""",
    )
    parser.add_argument("url", help="indirizzo di un singolo video")
    parser.add_argument(
        "--lang", default=",".join(DEFAULT_LANGS),
        help="lingue preferite dei sottotitoli, in ordine (default: %(default)s)",
    )
    parser.add_argument("-o", "--output", help="scrive su questo file invece che su stdout")
    parser.add_argument("--timestamps", action="store_true", help="timestamp a inizio paragrafo")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="modello mlx-whisper della trascrizione locale (default: %(default)s)",
    )
    parser.add_argument(
        "--no-fallback", action="store_true",
        help="non trascrivere l'audio in locale quando mancano i sottotitoli",
    )
    login = parser.add_mutually_exclusive_group()
    login.add_argument(
        "--cookies-from-browser", metavar="BROWSER",
        help="usa la sessione di questo browser (chrome, safari, firefox...) per i video che "
        "richiedono il login; senza, nessun accesso",
    )
    login.add_argument(
        "--cookies", metavar="FILE",
        help="usa i cookie di questo file in formato Netscape (cookies.txt) per i video che "
        "richiedono il login; yt-dlp può riscriverlo con i cookie aggiornati",
    )
    args = parser.parse_args(argv)
    if args.cookies is not None:
        cookies = Path(args.cookies).expanduser()
        if not cookies.is_file():
            parser.error(f"--cookies: file non trovato: {args.cookies}")
        args.cookies = str(cookies.resolve())
    args.langs = [lang.strip() for lang in args.lang.split(",") if lang.strip()]
    if not args.langs:
        parser.error("--lang: serve almeno una lingua")
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        text = transcript(
            args.url, args.langs, args.timestamps, args.model, not args.no_fallback,
            login_options(args.cookies_from_browser, args.cookies),
        )
    except TranscriptError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return exc.code
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Trascrizione salvata in {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
