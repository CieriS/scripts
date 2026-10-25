# Audio

`beat-frames.py` is documented in the [root README](../README.md#audio).

## `split-stems.py`

Splits one song, or every song of a folder, into stems and turns the guitar into MIDI notes, to study a part
that is hard to hear in the full mix. Everything runs locally; messages are in Italian.

```bash
uv run audio/split-stems.py ~/Desktop/brano.mp3                  # one song: output in ~/Desktop/stems
uv run audio/split-stems.py ~/Music/da-studiare                  # output in ~/Music/da-studiare/stems
uv run audio/split-stems.py ~/Music/da-studiare ~/Desktop/stems  # another output folder
uv run audio/split-stems.py cartella --shifts 1                  # twice as fast, a little less clean
uv run audio/split-stems.py cartella --midi guitar,other         # MIDI of "other" too
```

Requirements: `uv` and `ffmpeg` (`brew install ffmpeg`), a Mac with Apple Silicon for the GPU
(it works on the CPU too, much slower). The first run installs Demucs, PyTorch and Basic Pitch
in a cached environment and downloads the Demucs model; nothing is installed in the system.

### What it does

1. Takes the song given, or every `.wav`, `.mp3`, `.flac` and `.m4a` directly inside the
   folder given (not in subfolders). Quote paths with spaces or accents:
   `"~/Music/34_Artista - Titolo (Video).mp3"` does not expand `~`, use `$HOME/...` or no quotes
   around `~/`. Two files with the same name and different extensions would overwrite each
   other's stems, so the second one is skipped with a message.
2. Splits each song with Demucs `htdemucs_6s`, the model with a dedicated guitar stem, on the
   Mac GPU (`mps`); if that fails it retries on the CPU.
3. Measures the left, right and side levels of the guitar stem and writes the three channel
   files only when left and right really differ (see below); `guitar_channels.txt` keeps the
   levels and the verdict.
4. Converts the guitar stem into MIDI with Basic Pitch, keeping only notes in the guitar range
   (75–1400 Hz). Open the `.mid` file in GarageBand or Logic to see the notes on the piano roll.

Running it again only does what is missing: a song with all its files is skipped, a missing
MIDI or channel file is recreated, and a new `--midi` stem is added without splitting again.

```
<output>/
├── htdemucs_6s/<song>/
│   ├── vocals.wav  drums.wav  bass.wav  guitar.wav  piano.wav  other.wav
│   ├── guitar_channels.txt                          levels and verdict
│   └── guitar_left.wav  guitar_right.wav  guitar_side.wav   only when left and right differ
└── midi/
    └── <song>_guitar.mid      (and <song>_<stem>.mid for every --midi stem)
```

### Options

| Option | Default | Effect |
|---|---|---|
| `input` | — | One song, or a folder with songs |
| `output` | `stems/` next to the song, or inside the folder | Output folder |
| `--shifts N` | `2` | Demucs passes over shifted copies of the song: 2 is cleaner than 1 but takes twice as long |
| `--device auto\|mps\|cpu` | `auto` | `auto` tries the GPU, then the CPU |
| `--midi STEMS` | `guitar` | Stems to convert into MIDI, comma separated (`guitar,other`); empty for none |
| `--no-channels` | off | Skip the channel step: no levels measured, no channel files |

| Exit code | Meaning |
|---|---|
| `0` | Every song done (or nothing to do) |
| `1` | At least one song, channel split or MIDI failed: the others are still done, the errors are listed at the end |
| `2` | Wrong arguments, input not found, or a file that is not `.wav`, `.mp3`, `.flac`, `.m4a` |
| `3` | `ffmpeg` not found |

### When the guitars are mixed together

Demucs separates by timbre: two guitars, or a guitar and a synth or keyboard in the same
register, land in the same stem, and part of a guitar can end up in `other`. No model splits
two similar guitars reliably, so the script offers what works in practice, in this order:

1. **Listen to the channels.** Rock records often pan a rhythm guitar to one side and a second
   guitar to the other. `guitar_left.wav` and `guitar_right.wav` then hold one guitar each;
   `guitar_side.wav` (left minus right) removes whatever sits in the middle (often vocals, bass
   and a centered lead) and keeps what is panned apart. When a guitar is centered, the channels
   do not help, and the script does not write them: when the two sides are within 6 dB of each
   other and side is more than 6 dB below the louder channel, left and right are near copies
   of `guitar.wav`. A guitar panned all the way to one side (one channel at least 6 dB louder
   than the other) always gets its channel files. They are not written either
   when the guitar stem is practically empty (below -60 dB): then listen to `other.wav`.
   `guitar_channels.txt` says which case it was and with which threshold: a song measured with
   another threshold is measured again on the next run, and so is one whose measure failed
   (reported as an error). To force a new measure, delete that file.
2. **Check `other`.** When the guitar sounds thin or comes and goes in `guitar.wav`, the rest
   is in `other.wav`: run again with `--midi guitar,other` and read both MIDI files.
3. **Trust the notes in the guitar range.** The MIDI keeps only 75–1400 Hz, which drops most of
   a voice or synth above or below the guitar; notes that fall inside the range and do not fit
   the part are likely another instrument.
4. **Slow it down by ear.** For a short passage, play the clearest file at a lower speed
   without changing the pitch (in GarageBand, or `ffmpeg -i guitar_left.wav -af atempo=0.5
   slow.wav`).

### Limitations

- The CPU fallback runs after any Demucs failure, not only a GPU problem: a corrupted file is
  tried again on the CPU, slowly, before being reported.
- The separation is approximate: expect some bleed between stems, more with distorted or
  layered guitars. The `piano` stem of `htdemucs_6s` is the weakest of the six.
- Basic Pitch writes one MIDI track for everything it hears: chords and two guitars playing
  together come out as overlapping notes, and fast passages or heavy distortion give extra or
  missing notes. Read the MIDI as a guide, not a score.
- Time and space, measured on 2026-10-25 on a real 4-minute pop song (GPU, `--shifts 2`):
  about 30 seconds in all (separation alone about 19 s), and about 330 MB of files: six 44 MB
  stems (16-bit WAV), the MIDI files, and three 22 MB mono channel files when written. Delete the stems you
  do not need.
- On that song the guitar was in the middle (left -20.4 dB, right -20.5 dB, side -29.5 dB):
  no channel files were written. The 6 dB threshold is a rule of thumb checked on that song
  and on synthetic tones panned apart, not on a large set of recordings.
- The tests use a fake runner instead of Demucs, ffmpeg and Basic Pitch.
