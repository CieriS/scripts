# Video

## `transcript.py`

Prints the full transcript of one online video as clean, readable text, ready to paste
somewhere else for analysis. It works with every site yt-dlp supports (YouTube, Instagram,
TikTok, X, Facebook, Vimeo, Reddit…), but how much works **without logging in** depends on
the site: see [Sites without login](#sites-without-login). The output has no timestamps, tags
or repeated lines and is grouped into paragraphs. Nothing is left in the current directory: the
subtitles and the audio are downloaded into a temporary directory that is always removed,
also on errors. Messages are in Italian and go to stderr, so stdout holds only the transcript.

```bash
uv run video/transcript.py "https://youtu.be/VIDEO_ID" | pbcopy
uv run video/transcript.py "https://www.instagram.com/reel/ID/" --cookies-from-browser chrome
uv run video/transcript.py "https://www.tiktok.com/@user/video/ID" --cookies ~/cookies.txt
uv run video/transcript.py URL --lang en,it -o transcript.txt
uv run video/transcript.py URL --timestamps
```

Requirements: `uv` and `yt-dlp` (`brew install yt-dlp`). Keep yt-dlp up to date: the sites change often and
an old version stops reading subtitles or audio, so the first fix for any download error is
`brew upgrade yt-dlp`. The local fallback also needs
`ffmpeg` and a Mac with Apple Silicon; `uv` installs mlx-whisper from the dependency block at
the top of the file.

The output starts with a header:

```
Titolo: …
Autore: …
Piattaforma: Youtube
URL: https://www.youtube.com/watch?v=…
Fonte: sottotitoli manuali (it)

First paragraph…
```

### Options

| Option | Default | Effect |
|---|---|---|
| `url` | — | One video, `http(s)` on any site yt-dlp supports. YouTube URLs (`watch?v=`, `youtu.be/`, `/shorts/`, `/live/`, `/embed/`) are normalized and a `list=` parameter is dropped. Posts with several videos (carousels, threads, playlists) are rejected |
| `--lang it,en` | `it,en` | Preferred subtitle languages, in order. `en` also matches `en-US`, `en-GB` |
| `-o`, `--output FILE` | stdout | Write the transcript to a file |
| `--timestamps` | off | Start each paragraph with `[mm:ss]` (or `[h:mm:ss]`) |
| `--model REPO` | `mlx-community/whisper-large-v3-turbo` | mlx-whisper model (Hugging Face repository) of the local fallback, e.g. `mlx-community/whisper-small-mlx` for a faster, less accurate run |
| `--no-fallback` | off | Fail instead of downloading and transcribing the audio |
| `--cookies-from-browser NAME` | off | Use the login of this browser (`chrome`, `brave`, `firefox`, `safari`…) for sites that require it. Without it (or `--cookies`) no login is ever used |
| `--cookies FILE` | off | Use the login stored in a cookie file in Netscape format (`cookies.txt`), see [Login from a cookie file](#login-from-a-cookie-file). Cannot be combined with `--cookies-from-browser` |

| Exit code | Meaning |
|---|---|
| `0` | Transcript printed or written |
| `1` | Video private, unavailable, behind a login or with several videos; site not supported; no subtitles and no audio, or the local transcription failed or was disabled; empty transcript |
| `2` | Wrong arguments (also a `--cookies` file that does not exist, or both login options), not an `http(s)` URL, or a YouTube URL that is not a single video |
| `3` | `yt-dlp` not found |

### How the source is chosen

From the metadata returned by `yt-dlp --dump-single-json`, in this order:

1. **Manual subtitles** in the `--lang` languages, in their order.
2. **Manual subtitles** in the video's original language.
3. **Automatic captions** in the original language (the raw `xx-orig` track when present).
4. **Local transcription**: only the audio is downloaded and transcribed with mlx-whisper.

Before downloading the audio, the script checks the formats listed by yt-dlp: when all of
them are video only, it stops with an error instead of downloading a file without sound.

Automatic captions in other languages are YouTube's machine translations and are never used,
so a video in Spanish gives a Spanish transcript even with `--lang it,en`.

### Sites without login

Checked on 2026-10-21 with yt-dlp 2026.08.19, using the sample videos of yt-dlp's own tests:

| Site | Without login |
|---|---|
| YouTube | Works: subtitles, otherwise audio |
| Instagram | Metadata only: the files have no audio track, so there is nothing to transcribe. Carousels are recognized and rejected |
| TikTok, Reddit, Vimeo | yt-dlp is asked to log in |
| Facebook | yt-dlp cannot parse the page |
| X | Depends on the post |

So for Instagram, TikTok and the others `--cookies-from-browser` is needed: yt-dlp reads the
session of a browser where you are already logged in, locally, and nothing is stored. The
browser has to be one you use: a browser that was never opened has no cookies. Safari's
cookies can be read only after giving the terminal app Full Disk Access in System Settings;
Chrome-based browsers ask once for the keychain. Without access to the browser, use a
[cookie file](#login-from-a-cookie-file). The login paths have not been verified live yet.
When a site starts working without login in a newer yt-dlp, nothing needs to change.

### Login from a cookie file

`--cookies FILE` passes a cookie file to yt-dlp instead of reading a browser, so it works
without giving the terminal Full Disk Access for Safari. Export the cookies of the site once,
while logged in, with a browser extension that writes the Netscape format (`cookies.txt`, the
first line is `# Netscape HTTP Cookie File`); `~` and relative paths are accepted.

- The file is your session: anyone who has it is logged in as you. Keep it outside the
  repository (`.gitignore` skips files named like `cookies.txt`, but not every name) and delete it when you no longer need it.
- yt-dlp may rewrite the file at the end of a run with the updated cookies.
- When the session expires, the site asks for the login again: export a new file.
- A file that is not in Netscape format gives an error before any download.

Why a file next to `--cookies-from-browser`: Safari is the only browser logged in to the social
sites on this Mac, and reading its cookies needs Full Disk Access for the terminal app, which
also opens every other protected file to it. A file exported once gives the same login without
that access (decided in #13, 2026-11-03).

### Text cleanup

- Timing lines, `WEBVTT`/`NOTE`/`STYLE` blocks, tags (`<c>`, `<i>`, word timings, voices),
  HTML entities and sound annotations such as `[Musica]` or `♪` are removed.
- Automatic captions repeat the previous line at the top of every cue: for each cue the
  longest overlap between the words already written and the start of the cue is dropped.
- Manual subtitles only lose a cue identical to the previous one; nothing else is merged.
- Paragraphs: punctuated text breaks at a sentence end after 50 words, or at a pause of 4
  seconds after 20 words when the pause follows a sentence end. Text without punctuation (old
  automatic captions) breaks at pauses and every 120 words.

### Why mlx-whisper

The local engine had to run without manual installs and be fast on Apple Silicon.
mlx-whisper runs Whisper on the Mac's GPU through MLX and installs as an inline dependency;
`large-v3-turbo` is close to `large-v3` in accuracy at a fraction of the time. faster-whisper
is cross-platform but runs on the CPU on a Mac, several times slower; openai-whisper needs
PyTorch and is the slowest; whisper.cpp is fast but needs Homebrew and manual model
management. The dependency is limited to macOS arm64, so elsewhere the fallback fails with a
clear error while subtitles keep working.

### Known limitations

- **Automatic captions** are only as good as YouTube's speech recognition: wrong words, and
  often no punctuation or capitals, which gives coarser paragraphs. When the first word of a
  cue after a silence equals the last word written, it is dropped as an overlap.
- **Local transcription** takes time: the first run downloads the model (about 1.6 GB for
  `large-v3-turbo`, kept in the Hugging Face cache, `~/.cache/huggingface`), then a few
  seconds per minute of audio. In a test a 19-second video took about 6 minutes on the first
  run, almost all of it the download. Whisper can invent text over long silences or music.
- **The sites change often**: when subtitles or audio stop downloading, update yt-dlp first
  (`brew upgrade yt-dlp`).
- Most social sites need a login (see [Sites without login](#sites-without-login)), and a
  login-only video read through `--cookies-from-browser` uses your account: heavy use can make
  the site rate-limit or flag it.
- One video at a time: carousels and threads with several videos are rejected.
- The text of a post (caption, description) is not included, only what is said.
- The tests use a fake yt-dlp and a fake engine; the real paths were checked by hand.
