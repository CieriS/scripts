# YouTube

## `transcript.py`

Prints the full transcript of one YouTube video as clean, readable text: no timestamps, tags
or repeated lines, grouped into paragraphs. Nothing is left in the current directory: the
subtitles and the audio are downloaded into a temporary directory that is always removed,
also on errors. Messages are in Italian and go to stderr, so stdout holds only the transcript.

```bash
uv run youtube/transcript.py "https://youtu.be/VIDEO_ID" | pbcopy
uv run youtube/transcript.py URL --lang en,it -o transcript.txt
uv run youtube/transcript.py URL --timestamps
```

Requirements: `uv` and `yt-dlp` (`brew install yt-dlp`). The local fallback also needs
`ffmpeg` and a Mac with Apple Silicon; `uv` installs mlx-whisper from the dependency block at
the top of the file.

The output starts with a header:

```
Titolo: …
Canale: …
URL: https://www.youtube.com/watch?v=…
Fonte: sottotitoli manuali (it)

First paragraph…
```

### Options

| Option | Default | Effect |
|---|---|---|
| `url` | — | One video: `youtube.com/watch?v=`, `youtu.be/`, `/shorts/`, `/live/`, `/embed/`. A `list=` parameter is ignored: playlists are not supported |
| `--lang it,en` | `it,en` | Preferred subtitle languages, in order. `en` also matches `en-US`, `en-GB` |
| `-o`, `--output FILE` | stdout | Write the transcript to a file |
| `--timestamps` | off | Start each paragraph with `[mm:ss]` (or `[h:mm:ss]`) |
| `--model REPO` | `mlx-community/whisper-large-v3-turbo` | mlx-whisper model (Hugging Face repository) of the local fallback, e.g. `mlx-community/whisper-small-mlx` for a faster, less accurate run |
| `--no-fallback` | off | Fail instead of downloading and transcribing the audio |

| Exit code | Meaning |
|---|---|
| `0` | Transcript printed or written |
| `1` | Video private, unavailable or restricted; no subtitles and the local transcription failed or was disabled; empty transcript |
| `2` | Wrong arguments or not a single-video YouTube URL |
| `3` | `yt-dlp` not found |

### How the source is chosen

From the metadata returned by `yt-dlp --dump-single-json`, in this order:

1. **Manual subtitles** in the `--lang` languages, in their order.
2. **Manual subtitles** in the video's original language.
3. **Automatic captions** in the original language (the raw `xx-orig` track when present).
4. **Local transcription**: only the audio is downloaded and transcribed with mlx-whisper.

Automatic captions in other languages are YouTube's machine translations and are never used,
so a video in Spanish gives a Spanish transcript even with `--lang it,en`.

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
- **YouTube changes often**: when subtitles or audio stop downloading, update yt-dlp first
  (`brew upgrade yt-dlp`).
- Restricted videos (private, age-gated, members-only) are not read: the script does not use
  browser cookies.
- The tests use a fake yt-dlp and a fake engine; the real paths were checked by hand.
