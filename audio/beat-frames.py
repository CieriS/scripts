#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["librosa>=0.10"]
# ///
"""Detect the tempo of a song and convert beats into video frames, for cutting clips on the beat.

Usage:
    uv run audio/beat-frames.py <audio-file> [fps] [--duration SECONDS] [--min-bpm BPM]

fps defaults to 30. Only the first 90 seconds are analysed (--duration). When the detected
tempo is below --min-bpm (default 95) it is assumed to be half time and doubled; pass
--min-bpm 0 to keep it as detected. Output messages are in Italian.
"""

import argparse
import os
import sys

DEFAULT_FPS = 30.0
DEFAULT_DURATION = 90.0
DEFAULT_MIN_BPM = 95.0


def normalize_bpm(bpm, min_bpm=DEFAULT_MIN_BPM):
    """Double a half-time reading (e.g. 75 instead of 150) until it reaches min_bpm."""
    if bpm <= 0:
        raise ValueError("tempo non rilevato")
    while min_bpm > 0 and bpm < min_bpm:
        bpm *= 2
    return bpm


def frames_per_beat(bpm, fps):
    if fps <= 0:
        raise ValueError("fps deve essere positivo")
    return 60.0 / bpm * fps


def report(bpm, fps):
    fpb = frames_per_beat(bpm, fps)
    rows = [
        ("1 Battito (Video)", fpb),
        ("2 Battiti (Video)", fpb * 2),
        ("1/2 Battito (Foto)", fpb / 2),
    ]
    lines = [f"--- ANALISI RITMICA ({fps:g} FPS) ---", f"{'BPM rilevati':<20}: {bpm:.2f} BPM"]
    for label, frames in rows:
        lines.append(f"{label:<20}: {frames:.2f} frame (consigliato: {max(1, round(frames))} frame)")
    return "\n".join(lines)


def detect_bpm(path, duration):
    import librosa  # imported here so the pure functions above need no dependency

    y, sr = librosa.load(path, sr=None, duration=duration)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    # librosa >= 0.10 returns a one-element array, older versions a float
    return float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)


def main(argv=None):
    parser = argparse.ArgumentParser(description="BPM di un brano e battiti convertiti in frame.")
    parser.add_argument("path")
    parser.add_argument("fps", nargs="?", type=float, default=DEFAULT_FPS)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--min-bpm", type=float, default=DEFAULT_MIN_BPM)
    args = parser.parse_args(argv)
    if not os.path.isfile(args.path):
        print(f"Errore: file non trovato: {args.path}", file=sys.stderr)
        return 1
    try:
        bpm = normalize_bpm(detect_bpm(args.path, args.duration), args.min_bpm)
        print(report(bpm, args.fps))
    except ValueError as err:
        print(f"Errore: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
