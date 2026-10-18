#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "torch>=2.2",
#     "open_clip_torch>=2.24",
#     "pillow>=10",
#     "pillow-heif>=0.16",
#     "rawpy>=0.19",
# ]
# ///
"""Sort a set of photos and videos into folders by kind and subject, for video editing.

Usage:
    uv run media/sort-media.py <source> [<source> ...] --out <destination>
        [--move] [--dry-run] [--threshold 0.5] [--frames 5] [--device auto]

Every photo and video found under the sources (recursively) is classified locally with CLIP
(zero-shot, no training) as ambiente, cibo, persone or altro, and copied into
<destination>/{foto,video}/<category>/. Files below the confidence threshold go to `incerti/`,
files that cannot be decoded to `illeggibili/`.
Videos are classified on a few frames extracted with ffmpeg. Sidecar files (.xmp, .aae, Google
Takeout .json) follow their media. Originals are never modified: files are copied unless
--move is given, and nothing is overwritten. A CSV report of every decision is written to the
destination. Output messages are in Italian.
"""

import argparse
import csv
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

PHOTO_EXT = {
    ".jpg", ".jpeg", ".jpe", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif",
    ".tiff", ".avif",
}
RAW_EXT = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef", ".srw"}
VIDEO_EXT = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp", ".mts", ".m2ts", ".mpg",
    ".mpeg", ".wmv", ".insv", ".lrv",
}
SIDECAR_EXT = {".xmp", ".aae", ".json"}
SKIP_DIRS = {"__MACOSX", "@eaDir", "$RECYCLE.BIN", "System Volume Information"}

KIND_DIR = {"photo": "foto", "video": "video"}
UNCERTAIN = "incerti"
UNREADABLE = "illeggibili"
# Several phrasings per category, averaged (prompt ensembling): steadier than a single phrase.
# "altro" catches what is none of the three, so a screenshot is not forced into "cibo".
PROMPTS = {
    "ambiente": [
        "a photo of a landscape", "a photo of nature", "a photo of the sea",
        "a photo of mountains", "a photo of a city street", "a photo of a building",
        "a photo of the sky", "a photo of a room interior",
    ],
    "cibo": [
        "a photo of food", "a photo of a dish on a plate", "a photo of a meal",
        "a photo of a drink", "a photo of a dessert", "a photo of a pizza",
    ],
    "persone": [
        "a photo of a person", "a photo of people", "a selfie",
        "a portrait of a person", "a group photo of friends", "a photo of a child",
    ],
    "altro": [
        "a screenshot", "a photo of a document", "a photo of text",
        "a photo of an animal", "a photo of an object", "a black image",
    ],
}
CATEGORIES = tuple(PROMPTS)

DEFAULT_THRESHOLD = 0.5
DEFAULT_FRAMES = 5
MODEL = ("ViT-B-32", "laion2b_s34b_b79k")


class Unreadable(Exception):
    """The file cannot be decoded: it goes to `illeggibili/` with the reason in the report."""


class NotAnalysed(Exception):
    """The file is fine but could not be classified (no ffmpeg): it goes to `incerti/`."""


@dataclass
class Item:
    path: Path
    kind: str
    sidecars: list = field(default_factory=list)


@dataclass
class Row:
    source: str
    destination: str
    kind: str
    category: str
    confidence: float
    scores: str
    action: str
    note: str = ""


# --- pure logic ---------------------------------------------------------------------------

def media_kind(path):
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXT or ext in RAW_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return None


def is_sidecar_of(sidecar, media):
    """IMG_1.xmp, IMG_1.AAE, IMG_1.jpg.xmp, IMG_1.jpg.json, IMG_1.jpg.supplemental-metadata.json."""
    sidecar, media = Path(sidecar), Path(media)
    if sidecar.parent != media.parent or sidecar.suffix.lower() not in SIDECAR_EXT:
        return False
    name = sidecar.name.casefold()
    return sidecar.stem.casefold() == media.stem.casefold() or name.startswith(
        media.name.casefold() + "."
    )


def sidecar_name(sidecar, media, new_media):
    """Name of a sidecar once its media is renamed: IMG_1.xmp follows IMG_1 (1).jpg."""
    if sidecar.casefold().startswith(media.casefold() + "."):
        return new_media + sidecar[len(media):]
    old_stem, new_stem = Path(media).stem, Path(new_media).stem
    return new_stem + sidecar[len(old_stem):]


def decide(probs, threshold):
    if not probs:
        return UNCERTAIN, 0.0
    category = max(probs, key=probs.get)  # on a tie the first category wins
    confidence = probs[category]
    return (category if confidence >= threshold else UNCERTAIN), confidence


def average(prob_list):
    prob_list = [p for p in prob_list if p]
    if not prob_list:
        return {}
    keys = prob_list[0].keys()
    return {k: sum(p[k] for p in prob_list) / len(prob_list) for k in keys}


def frame_times(duration, count):
    """Evenly spaced timestamps that skip the very start and end (often black or shaky)."""
    if not duration or duration <= 0:
        return [0.0]
    return [duration * (i + 1) / (count + 1) for i in range(count)]


def format_scores(probs):
    return " ".join(f"{k}={v:.2f}" for k, v in probs.items())


def is_inside(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    return path == parent or parent in path.parents


# --- filesystem ---------------------------------------------------------------------------

def scan(sources, out):
    """Media found under the sources, plus the files ignored with the reason."""
    out = Path(out).resolve()
    items, ignored, seen = [], [], set()
    for source in sources:
        for dirpath, dirnames, filenames in os.walk(source):
            here = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and d not in SKIP_DIRS and (here / d).resolve() != out
            )
            media, others = [], []
            for name in sorted(filenames):
                path = here / name
                if name.startswith("."):
                    continue  # .DS_Store, AppleDouble ._ files, hidden files
                if path.is_symlink():
                    if path.absolute() not in seen:
                        seen.add(path.absolute())
                        ignored.append((path, "collegamento simbolico"))
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue  # nested or repeated sources
                seen.add(resolved)
                kind = media_kind(path)
                if kind:
                    media.append(Item(path, kind))
                else:
                    others.append(path)
            # photos first: a Live Photo's IMG_1.AAE belongs to IMG_1.HEIC, not IMG_1.MOV
            by_priority = sorted(media, key=lambda m: m.kind != "photo")
            for other in others:
                owner = next((m for m in by_priority if is_sidecar_of(other, m.path)), None)
                if owner:
                    owner.sidecars.append(other)
                else:
                    ignored.append((other, "non è una foto né un video"))
            items.extend(media)
    return items, ignored


def same_content(a, b):
    if a.stat().st_size != b.stat().st_size:
        return False
    return file_hash(a) == file_hash(b)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_destination(dest_dir, src, taken, name=None):
    """Free path for src in dest_dir (as `name`, default its own), or None when an identical
    file is already there.

    `taken` holds the casefolded paths planned in this run (case-insensitive like macOS), so a
    dry run and two same-named files from different folders do not collide.
    """
    name = name or src.name
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = dest_dir / name, 1
    while candidate.exists() or str(candidate).casefold() in taken:
        if candidate.exists() and candidate.is_file() and same_content(src, candidate):
            return None
        candidate = dest_dir / f"{stem} ({n}){suffix}"
        n += 1
    taken.add(str(candidate).casefold())
    return candidate


def transfer(src, dest, move):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(src, dest)  # keeps the modification time


# --- orchestration ------------------------------------------------------------------------

@dataclass
class Summary:
    rows: list
    counts: dict
    errors: int
    report: Path = None


def run(items, ignored, out, scorer, threshold=DEFAULT_THRESHOLD, move=False, dry_run=False,
        log=lambda msg: None):
    out = Path(out)
    rows, counts, errors, taken = [], {}, 0, set()
    for index, item in enumerate(items, 1):
        note = ""
        try:
            probs = scorer(item)
            category, confidence = decide(probs, threshold)
        except Unreadable as err:
            probs, note, category, confidence = {}, str(err), UNREADABLE, 0.0
        except NotAnalysed as err:
            probs, note, category, confidence = {}, str(err), UNCERTAIN, 0.0
        dest = unique_destination(out / KIND_DIR[item.kind] / category, item.path, taken)
        if dest is None:
            action = "duplicato"
            note = note or "già presente nella destinazione, lasciato al suo posto"
        else:
            action = "spostato" if move else "copiato"
            if dry_run:
                action = "da spostare" if move else "da copiare"
            else:
                try:
                    transfer(item.path, dest, move)
                    for sidecar in item.sidecars:
                        name = sidecar_name(sidecar.name, item.path.name, dest.name)
                        target = unique_destination(dest.parent, sidecar, taken, name)
                        if target is not None:  # never overwrite, skip an identical copy
                            transfer(sidecar, target, move)
                except OSError as err:
                    action, note, errors = "errore", f"errore: {err}", errors + 1
        key = f"{KIND_DIR[item.kind]}/{category}"
        if action not in ("errore", "duplicato"):
            counts[key] = counts.get(key, 0) + 1
        rows.append(Row(str(item.path), str(dest or ""), KIND_DIR[item.kind], category,
                        round(confidence, 3), format_scores(probs), action, note))
        log(f"[{index}/{len(items)}] {key:<16} {confidence:.2f}  {item.path.name}"
            + (f"  ({note})" if note else ""))
    for path, reason in ignored:
        rows.append(Row(str(path), "", "", "", 0.0, "", "ignorato", reason))
    summary = Summary(rows, counts, errors)
    if not dry_run and rows:
        summary.report = write_report(rows, out)
    return summary


def write_report(rows, out):
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"report-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    n = 1
    while path.exists():
        path = out / f"report-{time.strftime('%Y%m%d-%H%M%S')}-{n}.csv"
        n += 1
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sorgente", "destinazione", "tipo", "categoria", "confidenza",
                         "punteggi", "azione", "nota"])
        for r in rows:
            writer.writerow([r.source, r.destination, r.kind, r.category, r.confidence,
                             r.scores, r.action, r.note])
    return path


# --- video frames (ffmpeg) ----------------------------------------------------------------

def video_duration(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def extract_frames(path, count, workdir):
    """JPEG frames of the video in workdir; an empty list when nothing could be decoded."""
    frames = []
    for i, t in enumerate(frame_times(video_duration(path), count)):
        target = Path(workdir) / f"frame-{i}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
                 "-frames:v", "1", "-vf", "scale=448:-2", str(target)],
                capture_output=True, timeout=120, check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if target.exists() and target.stat().st_size > 0:
            frames.append(target)
    return frames


# --- CLIP classifier (adapter) ------------------------------------------------------------

class ClipScorer:
    """Loads the model on first use, so a run with nothing to classify downloads nothing."""

    def __init__(self, device="auto", frames=DEFAULT_FRAMES, log=lambda msg: None):
        self.device_name, self.frames, self.log = device, frames, log
        self.model = None
        self.has_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    def _load(self):
        import open_clip
        import pillow_heif
        import torch

        pillow_heif.register_heif_opener()
        device = self.device_name
        if device == "auto":
            device = ("mps" if torch.backends.mps.is_available()
                      else "cuda" if torch.cuda.is_available() else "cpu")
        self.log(f"Carico il modello {MODEL[0]} su {device} (il primo avvio lo scarica)...")
        model, _, preprocess = open_clip.create_model_and_transforms(MODEL[0], pretrained=MODEL[1])
        tokenizer = open_clip.get_tokenizer(MODEL[0])
        model = model.to(device).eval()
        with torch.no_grad():
            features = []
            for category in CATEGORIES:
                emb = model.encode_text(tokenizer(PROMPTS[category]).to(device))
                emb = emb / emb.norm(dim=-1, keepdim=True)
                mean = emb.mean(dim=0)
                features.append(mean / mean.norm())
        self.torch, self.model, self.preprocess, self.device = torch, model, preprocess, device
        self.text = torch.stack(features)

    def __call__(self, item):
        if self.model is None:
            self._load()
        if item.kind == "photo":
            return self._score([load_image(item.path)])
        if not self.has_ffmpeg:
            raise NotAnalysed("ffmpeg non installato: video non analizzato")
        with tempfile.TemporaryDirectory() as tmp:
            frames = extract_frames(item.path, self.frames, tmp)
            images = []
            for frame in frames:
                try:
                    images.append(load_image(frame))
                except Unreadable:
                    pass
            if not images:
                raise Unreadable("nessun fotogramma leggibile")
            return self._score(images)

    def _score(self, images):
        torch = self.torch
        batch = torch.stack([self.preprocess(img) for img in images]).to(self.device)
        with torch.no_grad():
            emb = self.model.encode_image(batch)
            emb = emb / emb.norm(dim=-1, keepdim=True)
            probs = (100.0 * emb @ self.text.T).softmax(dim=-1).cpu().tolist()
        return average([dict(zip(CATEGORIES, p)) for p in probs])


def load_image(path):
    from PIL import Image, ImageOps

    path = Path(path)
    try:
        if path.suffix.lower() in RAW_EXT:
            return _load_raw(path)
        with Image.open(path) as img:
            img.seek(0)  # first frame of an animated GIF/WebP
            return ImageOps.exif_transpose(img).convert("RGB")
    except Unreadable:
        raise
    except Exception as err:  # Pillow raises many types for broken or unsupported files
        raise Unreadable(f"illeggibile: {type(err).__name__}") from err


def _load_raw(path):
    import io

    import rawpy
    from PIL import Image

    with rawpy.imread(str(path)) as raw:
        try:
            thumb = raw.extract_thumb()  # the camera's embedded JPEG: fast and already developed
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return Image.open(io.BytesIO(thumb.data)).convert("RGB")
            return Image.fromarray(thumb.data).convert("RGB")
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            return Image.fromarray(raw.postprocess(half_size=True))


# --- command line -------------------------------------------------------------------------

def probability(value):
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("deve essere tra 0 e 1")
    return number


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("deve essere almeno 1")
    return number


def make_scorer(args, log):
    return ClipScorer(device=args.device, frames=args.frames, log=log)


def main(argv=None, scorer_factory=make_scorer):
    parser = argparse.ArgumentParser(
        description="Divide foto e video per tipo e soggetto (ambiente, cibo, persone, altro)."
    )
    parser.add_argument("sources", nargs="+", type=Path, help="cartelle da analizzare")
    parser.add_argument("--out", required=True, type=Path, help="cartella di destinazione")
    parser.add_argument("--move", action="store_true", help="sposta invece di copiare")
    parser.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe, senza scrivere")
    parser.add_argument("--threshold", type=probability, default=DEFAULT_THRESHOLD)
    parser.add_argument("--frames", type=positive_int, default=DEFAULT_FRAMES)
    parser.add_argument("--device", choices=["auto", "mps", "cuda", "cpu"], default="auto")
    args = parser.parse_args(argv)

    def log(msg):
        print(msg, file=sys.stderr, flush=True)

    for source in args.sources:
        if not source.is_dir():
            log(f"Errore: cartella non trovata: {source}")
            return 1
        if is_inside(source, args.out):
            log(f"Errore: la sorgente {source} è dentro la destinazione {args.out}")
            return 1
    if args.out.exists() and not args.out.is_dir():
        log(f"Errore: la destinazione esiste e non è una cartella: {args.out}")
        return 1

    items, ignored = scan(args.sources, args.out)
    if not items:
        print(f"Nessuna foto o video trovati ({len(ignored)} file ignorati).")
        return 0
    log(f"Trovati {len(items)} media ({len(ignored)} file ignorati).")
    summary = run(items, ignored, args.out, scorer_factory(args, log), args.threshold,
                  args.move, args.dry_run, log)

    print("\n--- RIEPILOGO ---" + (" (prova: nessun file scritto)" if args.dry_run else ""))
    for key in sorted(summary.counts):
        print(f"{key:<18}: {summary.counts[key]}")
    duplicates = sum(1 for r in summary.rows if r.action == "duplicato")
    print(f"{'duplicati saltati':<18}: {duplicates}")
    print(f"{'ignorati':<18}: {len(ignored)}")
    print(f"{'errori':<18}: {summary.errors}")
    if summary.report:
        print(f"Report: {summary.report}")
    return 1 if summary.errors else 0


if __name__ == "__main__":
    sys.exit(main())
