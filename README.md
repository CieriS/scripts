# scripts

Small personal automation scripts, grouped by area. Each script is self-contained: nothing to
build, and no dependencies to install by hand (the audio, media and video scripts declare their
own and run with `uv run`).

| Area | Folder | What is inside |
|---|---|---|
| Shell utilities | [`sh/`](sh/) | Bash helpers for the local filesystem |
| Instagram | [`instagram/`](instagram/) | Read-only browser-console script to see who does not follow back |
| Audio | [`audio/`](audio/) | Song tempo converted into video frames; songs split into stems and the guitar into MIDI |
| Media | [`media/`](media/) | Photos and videos sorted by kind and subject (landscape, food, people), for editing |
| Video | [`video/`](video/) | Full transcript of an online video (YouTube, Instagram, TikTok…) as clean text, from subtitles or local speech recognition |
| Processes | [`devps/`](devps/) | Read-only list of running development processes and containers: age, usage, exposed ports |

## Where to look

| You want to | Go to |
|---|---|
| Find which script does what | [Structure](#structure) |
| Run the shell helper | [Shell utilities](#shell-utilities) |
| Run the Instagram script | [`instagram/README.md`](instagram/README.md) |
| Find how many frames a beat lasts | [Audio](#audio) |
| Split songs into stems, guitar into MIDI | [`audio/README.md`](audio/README.md) |
| Sort photos and videos by subject | [`media/README.md`](media/README.md) |
| Get the transcript of a video | [`video/README.md`](video/README.md) |
| See forgotten development processes | [`devps/README.md`](devps/README.md) |
| Check your changes or add a script | [Development](#development) |
| Understand why the repository is shaped this way | [Decisions](#decisions) |
| Know what is not covered yet | [Known limitations](#known-limitations) |
| Know whether you may reuse the code | [Licence](#licence) |

## Structure

```
.
├── sh/
│   └── existingFileWithString.sh   # list the files of a directory that contain a string
├── instagram/
│   ├── README.md                   # usage, commands, how to read the results
│   └── ig-who-unfollowed.js        # read-only scan, snapshots and diff between scans
├── audio/
│   ├── README.md                   # split-stems: usage, output, mixed guitars, limitations
│   ├── beat-frames.py              # BPM of a song and beats converted into video frames
│   └── split-stems.py              # songs split into stems with Demucs, guitar into MIDI
├── media/
│   ├── README.md                   # usage, result, supported files, edge cases, limitations
│   └── sort-media.py               # photos/videos sorted by subject with a local CLIP model
├── video/
│   ├── README.md                   # usage, options, source choice, limitations
│   └── transcript.py               # transcript of a video: subtitles, else local mlx-whisper
├── devps/
│   ├── README.md                   # sections, selection rules, limitations
│   └── devps.py                    # running development processes, containers, TCP listeners
├── tests/
│   ├── existingFileWithString.test.sh   # Bash tests of the shell helper
│   ├── ig-who-unfollowed.test.js        # Node tests of the Instagram script
│   ├── fake-browser.js                  # fake browser and fake Instagram used by the tests
│   ├── test_beat_frames.py              # Python tests of the audio script (no librosa needed)
│   ├── test_split_stems.py              # Python tests of the stems script (fake Demucs and Basic Pitch)
│   ├── test_sort_media.py               # Python tests of the media script (fake classifier)
│   ├── test_transcript.py               # Python tests of the video script (fake yt-dlp)
│   ├── test_devps.py                    # Python tests of devps (invented ps, lsof and docker output)
│   └── fixtures/video/                  # hand-written WebVTT files, invented text only
├── check.sh                        # lint, syntax check and tests (also run by CI)
└── .github/                        # CI workflow and Dependabot
```

## Shell utilities

### `sh/existingFileWithString.sh`

Lists the files directly inside a directory (not recursive) that contain a given string and
prints how many files were scanned and how many matched. The string is matched literally, not
as a regular expression. Backslashes in the directory path are converted to `/`, so
Windows-style paths work from Git Bash. Output messages are in Italian.

```bash
bash sh/existingFileWithString.sh "TODO" /path/to/directory
```

| Exit code | Meaning |
|---|---|
| `0` | Scan completed, with or without matches |
| `1` | Directory not found |
| `2` | Wrong arguments: both are required and the string cannot be empty |

## Instagram

`ig-who-unfollowed.js` is pasted in the browser console on `instagram.com` while logged in. It
only sends GET requests, so it cannot change the account. Usage, commands and how to read the
results are in [`instagram/README.md`](instagram/README.md).

## Audio

### `audio/beat-frames.py`

Detects the tempo of a song with [librosa](https://librosa.org) and prints how many video
frames one beat, two beats and half a beat last at a given frame rate, rounded to the nearest
whole frame: the clip lengths for cutting a video (or a photo slideshow) on the beat. Only the
first 90 seconds are analysed. librosa often reads a fast song at half its tempo (75 instead
of 150), so a tempo below 95 BPM is doubled until it reaches 95. Output messages are in Italian.

```bash
uv run audio/beat-frames.py song.mp3 30                  # 30 fps (default)
uv run audio/beat-frames.py song.mp3 24 --min-bpm 0      # keep a slow tempo as detected
uv run audio/beat-frames.py song.mp3 60 --duration 30    # analyse only the first 30 seconds
```

`uv` reads the dependency list at the top of the file and installs librosa in a cached
environment on the first run. The detected tempo is approximate (a 120 BPM click track reads
as about 117.5): check the suggested frame counts against the timeline.

| Exit code | Meaning |
|---|---|
| `0` | Analysis completed |
| `1` | File not found, or no tempo detected (e.g. silence) |
| `2` | Wrong arguments |

### `audio/split-stems.py`

Splits one song, or every song of a folder, into six stems with [Demucs](https://github.com/adefossez/demucs)
(vocals, drums, bass, guitar, piano, other) on the Mac GPU, converts the guitar into MIDI with
[Basic Pitch](https://github.com/spotify/basic-pitch) to read its notes on a piano roll, and
splits the guitar stem into its left and right channels, where two guitars panned apart can be
heard one at a time. Songs already done are skipped.

```bash
uv run audio/split-stems.py ~/Desktop/brano.mp3     # or a folder of songs
```

Output, options, what to do when the guitars are mixed together, and limitations are in
[`audio/README.md`](audio/README.md).

## Media

`sort-media.py` copies the photos and videos of one or more folders into
`foto|video/{ambiente,cibo,persone,altro,incerti,illeggibili}/`, classifying each file locally
with CLIP (videos on a few frames extracted with ffmpeg), and writes a CSV report of every
decision. Originals are never modified unless `--move` is given; `--dry-run` only shows the plan.

```bash
uv run media/sort-media.py ~/Desktop/viaggio --out ~/Desktop/ordinati --dry-run
```

Options, result layout, supported formats, edge cases and limitations are in
[`media/README.md`](media/README.md).

## Video

`transcript.py` prints the full transcript of one online video as readable paragraphs, headed
by title, author, platform, URL and the source used. It works on any site yt-dlp supports. It
reads manual subtitles, then automatic captions (no video download); when a video has none it
downloads only the audio and transcribes it locally with mlx-whisper on Apple Silicon. No
login is used unless `--cookies-from-browser` or `--cookies FILE` is given, which most social sites require.

```bash
uv run video/transcript.py "https://youtu.be/VIDEO_ID" | pbcopy
```

Options, source choice and limitations are in [`video/README.md`](video/README.md).

## Development

```bash
./check.sh
```

Runs `shellcheck` on every tracked `*.sh`, `node --check` on every tracked `*.js`,
`py_compile` on every tracked `*.py`, the Bash tests in `tests/*.test.sh`, the Node tests in
`tests/*.test.js` (built-in `node --test` runner) and the Python tests in `tests/test_*.py`
(`unittest`); nothing to install. It only looks at tracked files: `git add` a new file first. CI
runs the same command on every push to `development` and on Dependabot's pull requests.

Requirements: `bash`, `shellcheck`, `node`, `python3`; `uv` only to run the audio, media and
video scripts, `yt-dlp` for the video script, `ffmpeg` for the media script's video tests
(skipped without it).

Changes are made on a branch, checked with `./check.sh` and merged locally into `development`,
the default branch, with a fast-forward. Pull requests are used only by Dependabot.

Adding a script:

1. Put it in the folder of its area (create a new top-level folder for a new area).
2. Start the file with a comment that says what it does and how to run it.
3. Add tests: `tests/<name>.test.sh` for a shell script, `tests/<name>.test.js` for a browser
   script (see `tests/fake-browser.js` for how to run one under Node).
4. Add it to the structure above and document it in this README, or in the area's own
   `README.md` once the area has more than one script.
5. Never commit personal data: usernames, exports, tokens or cookies.

## Decisions

- **Grouped by area, not by language.** A folder is a topic (`instagram/`); `sh/` holds the
  generic shell helpers that belong to no topic yet.
- **One file per script, no build step.** The Instagram script is pasted whole into the
  browser console, so it must work alone: no modules, no bundling.
- **Read-only.** `ig-who-unfollowed.js` has no write request at all. An earlier version that
  could also unfollow (`ig-unfollowers.js`) was removed; unfollowing is done by hand from the
  profiles the script opens.
- **Own code only.** The third-party console tool these scripts replaced is not in the
  repository.
- **Python dependencies declared inside the script.** `beat-frames.py` needs librosa,
  `split-stems.py` Demucs and Basic Pitch, and
  `sort-media.py` needs PyTorch and OpenCLIP: the inline metadata block (PEP 723) lets `uv run`
  install them on demand, so the repository still has no setup step. The logic lives in plain
  functions and the heavy libraries are imported only where the audio or the images are read,
  so the tests run with the standard library alone.
- **Media are classified locally with CLIP, zero-shot.** No cloud service (private photos,
  cost per image) and no training: a category is a few English phrases, so adding one is a
  one-line change. Files are copied by default and never overwritten, so a wrong
  classification costs nothing.
- **A single check command.** `check.sh` is what runs locally and in CI, so the two cannot
  drift apart. Tests are plain Bash or Node's built-in runner, in line with the no-dependency
  rule.
- **Browser scripts are tested as they are.** The tests load the real file into a fake page
  with fake Instagram endpoints and call its public commands, so the script needs no exports
  or test hooks.
- **Local fast-forward merges instead of pull requests**, so every commit on `development`
  carries the author's own identity.
- **GitHub Actions pinned by commit SHA**, updated by Dependabot.

## Known limitations

- The Instagram script is tested against fake endpoints only. It relies on Instagram's private
  web endpoints, which can change without notice and cannot be exercised in CI.
- Its completeness check needs the profile numbers passed by hand to `IU.scan()`: without
  them a truncated list cannot be detected.
- `sort-media.py` puts each file in one category only, and its tests use a fake classifier:
  the real model's accuracy is checked by hand on real photos (see
  [`media/README.md`](media/README.md#limitations)).
- `transcript.py` is tested with a fake yt-dlp and fake transcription engine: the sites' real
  responses and mlx-whisper are checked by hand (see
  [`video/README.md`](video/README.md#known-limitations)).
- `beat-frames.py` assumes a constant tempo: songs that speed up or change tempo get a single
  average value. The half-time correction is a threshold, not a detection, so a genuinely slow
  song needs `--min-bpm 0`. The tests cover the arithmetic only; librosa's detection is checked
  by hand on a real file.

## Git hooks

`.githooks/pre-push` runs before every push: a secret scan of the outgoing commits (gitleaks, or its Docker image when the binary is missing) and then the fast checks (`./check.sh`). Enable it once per clone with `git config core.hooksPath .githooks`. Skip it once with `git push --no-verify`. On CI, `.github/workflows/secret-scan.yml` scans the whole history of every branch and tag.

## Licence

No licence is granted: the code is public to be read, not to be reused.
