#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10,<3.12"
# dependencies = [
#     "demucs>=4.1",
#     "basic-pitch>=0.4",
#     "soundfile>=0.12",
#     "setuptools<81",
# ]
# ///
"""Split every song of a folder into stems, and the guitar into MIDI notes, to study a part.

Usage:
    uv run audio/split-stems.py <song-or-folder> [output-folder] [--shifts 2] [--device auto]
        [--midi guitar,other] [--no-channels]

The input is one song, or a folder: then every .wav, .mp3, .flac and .m4a directly inside it
(not in subfolders) is split
with Demucs (model htdemucs_6s: vocals, drums, bass, guitar, piano, other) on the Mac GPU,
falling back to the CPU. Songs already split are skipped. The output folder defaults to
stems/ next to the song, or inside the folder:

    stems/htdemucs_6s/<song>/{vocals,drums,bass,guitar,piano,other}.wav
    stems/htdemucs_6s/<song>/guitar_{left,right,side}.wav   only when left and right differ
    stems/htdemucs_6s/<song>/guitar_channels.txt            levels and why

    stems/midi/<song>_<stem>.mid                            Basic Pitch notes

The guitar stem is also split into its left and right channels and into "side" (left minus
right): rock records often pan two guitars to opposite sides, so one channel can hold one
guitar alone. When the guitar is in the middle the two channels are near copies, so they are
written only when side is no more than 6 dB below the louder channel. MIDI notes are limited to the guitar range (about 75-1400 Hz) to drop most of
a synth or a voice that leaked in. Output messages are in Italian.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MODEL = "htdemucs_6s"
STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")
AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a"}
DEFAULT_SHIFTS = 2
DEFAULT_MIDI_STEMS = ("guitar",)
# guitar range: low E (82 Hz, or lower with drop tunings) up to the 24th fret of the high E (1319 Hz)
MIDI_MIN_HZ = 75
MIDI_MAX_HZ = 1400
CHANNELS_REPORT = "guitar_channels.txt"
# side this much below the louder channel = the two channels are nearly the same
SIDE_GAP_DB = 6.0
# below this the guitar stem is practically empty (no guitar, or it went into another stem)
SILENT_DB = -60.0
CHANNELS = {
    "left": "pan=mono|c0=c0",
    "right": "pan=mono|c0=c1",
    "side": "pan=mono|c0=0.5*c0-0.5*c1",
}

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_FFMPEG = 3


@dataclass(frozen=True)
class Song:
    path: Path
    name: str


# --- pure functions -------------------------------------------------------------------------


def find_songs(folder):
    """Audio files directly inside the folder, sorted; hidden files (._ from macOS) skipped.

    Returns (songs, duplicates): two files with the same name and different extensions
    would write the same stem folder, so only the first one is kept.
    """
    songs, duplicates, seen = [], [], set()
    for path in sorted(Path(folder).iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in AUDIO_EXT:
            continue
        if path.stem in seen:
            duplicates.append(path)
            continue
        seen.add(path.stem)
        songs.append(Song(path, path.stem))
    return songs, duplicates


def resolve_input(path):
    """Return (songs, duplicates, default output folder) for a song file or a folder.

    Raises ValueError with an Italian message when the path cannot be used.
    """
    path = Path(path).expanduser()
    if path.is_dir():
        songs, duplicates = find_songs(path)
        return songs, duplicates, path / "stems"
    if path.is_file():
        if path.suffix.lower() not in AUDIO_EXT:
            raise ValueError(f"formato non supportato: {path.name} (validi: .wav, .mp3, .flac, .m4a)")
        return [Song(path, path.stem)], [], path.parent / "stems"
    raise ValueError(f"file o cartella non trovati: {path}")


def stem_dir(out, song):
    return Path(out) / MODEL / song.name


def stems_done(out, song):
    return all((stem_dir(out, song) / f"{stem}.wav").is_file() for stem in STEMS)


def channels_report(out, song):
    return stem_dir(out, song) / CHANNELS_REPORT


def channels_done(out, song):
    """The report is written last, after the channel files (if any): it marks the step as done."""
    return channels_report(out, song).is_file()


def parse_mean_volume(ffmpeg_output):
    """Mean level in dB from ffmpeg's volumedetect output, or None (-inf for silence)."""
    match = re.search(r"mean_volume:\s*(-?inf|-?[0-9.]+)\s*dB", ffmpeg_output)
    return float(match.group(1)) if match else None


def channels_differ(levels):
    """True when left and right hold different parts, so writing them apart is useful.

    `levels` maps left/right/side to their mean level in dB. Side is left minus right: for a
    guitar in the middle it is far below the channels, for guitars panned apart it is close.
    """
    if any(levels.get(name) is None for name in CHANNELS):
        return False
    if is_silent(levels):
        return False
    return levels["side"] >= max(levels["left"], levels["right"]) - SIDE_GAP_DB


def is_silent(levels):
    loudest = [levels.get(name) for name in ("left", "right") if levels.get(name) is not None]
    return bool(loudest) and max(loudest) < SILENT_DB


def format_report(levels, differ):
    shown = ", ".join(
        f"{name} {'?' if levels.get(name) is None else f'{levels[name]:.1f} dB'}" for name in CHANNELS
    )
    if differ:
        verdict = "sinistro e destro diversi: scritti guitar_left/right/side.wav"
    elif is_silent(levels):
        verdict = "stem della chitarra quasi vuoto: canali non scritti (ascolta other.wav)"
    else:
        verdict = (
            f"sinistro e destro quasi uguali (side più basso di oltre {SIDE_GAP_DB:.0f} dB): "
            "chitarra al centro, canali non scritti"
        )
    return f"Livelli medi: {shown}\n{verdict}\n"


def missing_midi(out, song, stems):
    return [stem for stem in stems if not midi_path(out, song, stem).is_file()]


def demucs_command(python, song, out, device, shifts):
    command = [python, "-m", "demucs", "-n", MODEL, "-d", device, "--shifts", str(shifts)]
    if device == "mps":
        command += ["--overlap", "0.25"]
    return command + ["-o", str(out), str(song.path)]


def devices(choice):
    """Devices to try in order: the Mac GPU first, the CPU as a fallback."""
    return ["mps", "cpu"] if choice == "auto" else [choice]


def basic_pitch_command(python, wav, midi_dir):
    return [
        python, "-m", "basic_pitch.predict",
        "--minimum-frequency", str(MIDI_MIN_HZ), "--maximum-frequency", str(MIDI_MAX_HZ),
        str(midi_dir), str(wav),
    ]


def midi_path(out, song, stem):
    return Path(out) / "midi" / f"{song.name}_{stem}.mid"


def level_command(guitar_wav, pan):
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-i", str(guitar_wav),
        "-af", f"{pan},volumedetect", "-f", "null", "-",
    ]


def channel_commands(guitar_wav):
    """ffmpeg commands writing guitar_{left,right,side}.wav next to the guitar stem."""
    guitar_wav = Path(guitar_wav)
    return [
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(guitar_wav),
            "-af", pan, str(guitar_wav.with_name(f"guitar_{name}.wav")),
        ]
        for name, pan in CHANNELS.items()
    ]


def parse_stems(text):
    stems = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [s for s in stems if s not in STEMS]
    if unknown:
        raise ValueError(f"stem sconosciuti: {', '.join(unknown)} (validi: {', '.join(STEMS)})")
    return stems


# --- I/O ------------------------------------------------------------------------------------


def run(command):
    """Run a command, output shown to the user; True when it succeeded."""
    return subprocess.run(command).returncode == 0


def measure(command):
    """Run an ffmpeg level command and return its mean level in dB, or None."""
    result = subprocess.run(command, capture_output=True, text=True)
    return parse_mean_volume(result.stderr) if result.returncode == 0 else None


def split_channels(song, out, runner, meter):
    """Write the channel files only when left and right differ; always write the report."""
    guitar = stem_dir(out, song) / "guitar.wav"
    levels = {name: meter(level_command(guitar, pan)) for name, pan in CHANNELS.items()}
    differ = channels_differ(levels)
    if differ and not all(runner(c) for c in channel_commands(guitar)):
        return False
    if not differ:
        for name in CHANNELS:  # left over from an older run
            (stem_dir(out, song) / f"guitar_{name}.wav").unlink(missing_ok=True)
    report = format_report(levels, differ)
    channels_report(out, song).write_text(report, encoding="utf-8")
    print(f"  canali della chitarra: {report.splitlines()[1]}", flush=True)
    return True


def separate(song, out, device_choice, shifts, runner):
    for device in devices(device_choice):
        if runner(demucs_command(sys.executable, song, out, device, shifts)):
            return True
        print(f"  separazione su {device} fallita", file=sys.stderr)
    return False


def to_midi(song, out, stem, runner):
    """Basic Pitch refuses to overwrite and names files after the input: work in a temp dir."""
    target = midi_path(out, song, stem)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stems-midi-") as tmp:
        if not runner(basic_pitch_command(sys.executable, stem_dir(out, song) / f"{stem}.wav", tmp)):
            return False
        produced = sorted(Path(tmp).glob("*.mid"))
        if not produced:
            return False
        shutil.move(str(produced[0]), target)
    return True


def process(song, out, args, runner, meter=measure):
    """Split one song, doing only what is missing; returns the problems (empty when all went well)."""
    need_stems = not stems_done(out, song)
    need_channels = args.channels and (need_stems or not channels_done(out, song))
    need_midi = args.midi if need_stems else missing_midi(out, song, args.midi)
    if not (need_stems or need_channels or need_midi):
        print(f"✓ già fatto: {song.name}", flush=True)
        return []
    print(f"▶ {song.name}", flush=True)
    if need_stems and not separate(song, out, args.device, args.shifts, runner):
        return [f"{song.name}: separazione fallita"]
    problems = []
    if need_channels and not split_channels(song, out, runner, meter):
        problems.append(f"{song.name}: canali della chitarra non creati")
    for stem in need_midi:
        if not to_midi(song, out, stem, runner):
            problems.append(f"{song.name}: MIDI di {stem} non creato")
    return problems


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="split-stems.py",
        description="Separa in stem le canzoni di una cartella e trascrive la chitarra in MIDI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""esempi:
  uv run audio/split-stems.py ~/Desktop/brano.mp3              # un brano: stem in ~/Desktop/stems
  uv run audio/split-stems.py ~/Music/da-studiare
  uv run audio/split-stems.py ~/Music/da-studiare ~/Desktop/stems --shifts 1
  uv run audio/split-stems.py cartella --midi guitar,other    # se la chitarra finisce in other
  uv run audio/split-stems.py cartella --no-channels

exit code: 0 ok, 1 almeno un brano non riuscito, 2 argomenti, file o cartella non validi,
3 ffmpeg assente.""",
    )
    parser.add_argument("input", help="un brano, o una cartella di brani (.wav, .mp3, .flac, .m4a)")
    parser.add_argument("output", nargs="?", help="cartella di output (default: <input>/stems)")
    parser.add_argument(
        "--shifts", type=int, default=DEFAULT_SHIFTS,
        help="passate di Demucs: 2 pulisce meglio ma raddoppia il tempo, 1 è il più veloce "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--device", choices=("auto", "mps", "cpu"), default="auto",
        help="auto = GPU del Mac (mps), poi CPU se fallisce (default: %(default)s)",
    )
    parser.add_argument(
        "--midi", default=",".join(DEFAULT_MIDI_STEMS),
        help=f"stem da trascrivere in MIDI, separati da virgola, vuoto per nessuno "
        f"(valori: {', '.join(STEMS)}; default: %(default)s)",
    )
    parser.add_argument(
        "--no-channels", dest="channels", action="store_false",
        help="non creare guitar_left/right/side.wav",
    )
    args = parser.parse_args(argv)
    if args.shifts < 1:
        parser.error("--shifts deve essere almeno 1")
    try:
        args.midi = parse_stems(args.midi)
    except ValueError as exc:
        parser.error(f"--midi: {exc}")
    return args


def main(argv=None, runner=run, meter=measure):
    args = parse_args(argv)
    try:
        songs, duplicates, default_out = resolve_input(args.input)
    except ValueError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if shutil.which("ffmpeg") is None:
        print("Errore: ffmpeg non trovato: installalo con brew install ffmpeg", file=sys.stderr)
        return EXIT_NO_FFMPEG
    out = Path(args.output).expanduser() if args.output else default_out
    for path in duplicates:
        print(f"Salto {path.name}: c'è già un brano con lo stesso nome", file=sys.stderr)
    if not songs:
        print(f"Nessun brano (.wav, .mp3, .flac, .m4a) in {args.input}", file=sys.stderr)
        return EXIT_OK
    problems = []
    for song in songs:
        problems += process(song, out, args, runner, meter)
    print(f"Fatto. Stem in: {out / MODEL}")
    for problem in problems:
        print(f"Errore: {problem}", file=sys.stderr)
    return EXIT_FAILED if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
