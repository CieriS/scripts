# media

`sort-media.py` sorts a set of photos and videos into folders by kind and subject, to find the
right shots quickly while editing a video. Everything runs locally: no upload, no account, no
cost per file.

## Where to look

| You want to | Go to |
|---|---|
| Sort a folder | [Usage](#usage) |
| Know where each file ends up | [Result](#result) |
| Know which files are recognised | [Supported files](#supported-files) |
| Understand how a file is classified | [How it works](#how-it-works) |
| Understand a strange result | [Edge cases](#edge-cases) and [Limitations](#limitations) |
| Change the categories | [Changing the categories](#changing-the-categories) |

## Usage

Requirements: [`uv`](https://docs.astral.sh/uv/) and, for videos, `ffmpeg` (`brew install
ffmpeg`). `uv` reads the dependency list at the top of the script (PyTorch, OpenCLIP, Pillow,
HEIC and RAW readers) and installs it in a cached environment on the first run; the model
(about 600 MB) is downloaded on the first classification and cached as well.

```bash
uv run media/sort-media.py ~/Desktop/viaggio --out ~/Desktop/ordinati --dry-run   # see the plan
uv run media/sort-media.py ~/Desktop/viaggio --out ~/Desktop/ordinati             # copy
uv run media/sort-media.py card1 card2 --out ordinati                             # several sources
uv run media/sort-media.py viaggio --out ordinati --move                          # move instead
```

| Option | Default | Meaning |
|---|---|---|
| `--out` | (required) | Destination folder; created if missing |
| `--dry-run` | off | Classify and print the plan, write nothing (no files, no report) |
| `--move` | off | Move instead of copying; without it the originals are never touched |
| `--threshold` | `0.5` | Minimum confidence (0–1) to accept a category; below it the file goes to `incerti/`. `0` always picks a category |
| `--frames` | `5` | Frames sampled from each video |
| `--device` | `auto` | `mps` (Apple GPU), `cuda`, `cpu`; `auto` picks the first available |

| Exit code | Meaning |
|---|---|
| `0` | Done (also when no media was found) |
| `1` | A source is missing or inside the destination, the destination is a file, or at least one file could not be copied/moved (see the report) |
| `2` | Wrong arguments |

## Result

```
ordinati/
├── foto/
│   ├── ambiente/     landscapes, sea, mountains, streets, buildings, interiors
│   ├── cibo/         dishes, meals, drinks, desserts
│   ├── persone/      people, selfies, portraits, groups
│   ├── altro/        none of the above: screenshots, documents, animals, objects
│   ├── incerti/      no category reached the threshold
│   └── illeggibili/  the file could not be decoded (corrupt, empty, unsupported)
├── video/            same folders
└── report-YYYYMMDD-HHMMSS.csv
```

The report has one row per file: source, destination, kind, category, confidence, the score of
every category, the action (`copiato`, `spostato`, `duplicato`, `errore`, `ignorato`) and a
note with the reason when something was skipped. After a `--move`, the report is also the
record of where every file came from. Each run writes a new report and never overwrites an old one.

## Supported files

| Kind | Extensions (any case) |
|---|---|
| Photo | `jpg jpeg jpe png heic heif webp gif bmp tif tiff avif` |
| RAW photo | `dng cr2 cr3 nef arw raf orf rw2 pef srw` (the camera's embedded preview is used) |
| Video | `mp4 mov m4v avi mkv webm 3gp mts m2ts mpg mpeg wmv insv lrv` |
| Sidecar | `xmp aae json`: moved with their media, never classified on their own |

Everything else is listed in the report as `ignorato` and left where it is.

## How it works

1. **Scan**: every source is walked recursively. Photos and videos are recognised by extension.
2. **Classification**: [CLIP](https://github.com/mlfoundations/open_clip) (`ViT-B-32`,
   LAION-2B weights) compares the image with a few English phrases per category ("a photo of
   food", "a selfie", "a screenshot", …) and returns a probability per category. Nothing is
   trained: the categories are just text.
3. **Videos**: `ffmpeg` extracts `--frames` frames spread over the video, skipping the very start
   and end. Their probabilities are averaged, so the video goes where most of it belongs.
4. **Decision**: the category with the highest probability wins if it reaches `--threshold`,
   otherwise the file goes to `incerti/`.
5. **Copy or move** into `foto|video/<category>/`, with the original modification time.

## Edge cases

| Case | What happens |
|---|---|
| Same name from two folders, different content | The second becomes `name (1).ext` |
| Identical file already in the destination | Not copied again (`duplicato`), so a second run over the same folder adds only what is new. With `--move` the source of a duplicate stays where it is, it is never deleted |
| Two identical files with the same name in the sources | Only the first is copied, the second is a `duplicato` |
| Names that differ only in case (`IMG.jpg`, `img.JPG`) | Treated as the same name, as macOS does: the second is numbered |
| Sidecars (`IMG_1.xmp`, `IMG_1.AAE`, `IMG_1.jpg.json` from Google Takeout) | Follow their media, renamed with it if it is numbered; never overwrite an existing file |
| Live Photo (`IMG_1.HEIC` + `IMG_1.MOV`) | Photo and video are classified separately; the `.AAE` goes with the photo |
| Rotated photos (EXIF orientation) | Straightened before the classification |
| Animated GIF/WebP | The first frame is classified |
| Corrupt or empty file, fake extension, unsupported RAW | `illeggibili/`, with the reason in the report |
| Video without a picture (audio only) or that ffmpeg cannot decode | `illeggibili/` |
| `ffmpeg` not installed | Videos go to `incerti/` with the note `ffmpeg non installato`; photos are sorted normally |
| Hidden files and folders (`.DS_Store`, `._*`, `.thumbnails`), `__MACOSX`, `@eaDir`, `$RECYCLE.BIN`, `System Volume Information` | Skipped silently |
| Symbolic links | Not followed, listed as `ignorato` |
| Destination inside a source | Allowed: it is not scanned again |
| Source inside the destination (or the same folder) | Refused, exit code 1 |
| Sources nested or repeated on the command line | Each file is processed once |
| No photos or videos at all | Exits 0 without loading the model |
| A copy fails (disk full, permissions) | Recorded as `errore` in the report, the run continues, exit code 1 |

## Changing the categories

The categories are the keys of `PROMPTS` at the top of the script, and the folder names follow
them. To add one (e.g. `animali`), add a key with a few English phrases and remove the matching
phrase from `altro`. Several short, concrete phrases work better than one long phrase.

## Limitations

- **Accuracy depends on the picture.** Clean shots are classified well. Mixed ones (people at
  a table, a dish in front of the sea) go to the dominant subject, or to `incerti/` when it is
  close: check `incerti/` and the confidence column of the report.
- **One category per file.** A photo is never in two folders.
- **Duplicates are found by name and content.** The same photo saved under two different
  names is copied twice.
- **A dry run can number a file that a real run would skip.** It does not compare the content
  of two identical files that both come from the sources.
- **The tests do not run the model.** `tests/test_sort_media.py` covers scanning, decisions,
  names, duplicates, sidecars, errors, the command line and frame extraction with a fake
  classifier. The real classifier was checked by hand on every supported format with random
  weights (no network access to the model in that environment), so the categories themselves
  still need a check on real photos.
