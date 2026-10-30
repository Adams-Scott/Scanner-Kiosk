"""Scan module for the Scanner Kiosk (Fujitsu ScanSnap S1500 via SANE)."""
import shutil
import subprocess
import uuid
from pathlib import Path

import img2pdf
from PIL import Image

SCAN_ROOT = Path("/tmp/scan")

# Paper sizes (width x height in mm, portrait as fed). The first two are listed
# first in the web page.
PAPER_SIZES = {
    "letter":    ("Letter (8.5 × 11 in)",       215.9, 279.4),
    "legal":     ("Legal (8.5 × 14 in)",        215.9, 355.6),
    "a4":        ("A4 (210 × 297 mm)",          210.0, 297.0),
    "a5":        ("A5 (148 × 210 mm)",          148.0, 210.0),
    "a6":        ("A6 (105 × 148 mm)",          105.0, 148.0),
    "b5":        ("B5 (182 × 257 mm)",          182.0, 257.0),
    "executive": ("Executive (7.25 × 10.5 in)", 184.15, 266.7),
    "statement": ("Statement (5.5 × 8.5 in)",   139.7, 215.9),
    "card":      ("Business card (2 × 3.5 in)",  50.8,  88.9),
}
# Automatic size: scan the largest standard sheet, then let the driver crop
# each page to its detected edges.
AUTO_AREA = (215.9, 355.6)

RES_BW, RES_COLOR = 300, 200    # dpi

# What the Scan button always does.
BUTTON_SETTINGS = dict(size="letter", auto_size=False, color=False,
                       duplex=True, skip_blank=True)


def validate(settings: dict) -> dict:
    """Return clean scan settings, or raise ValueError."""
    out = dict(
        size=settings.get("size", "letter"),
        auto_size=bool(settings.get("auto_size", False)),
        color=bool(settings.get("color", False)),
        duplex=bool(settings.get("duplex", True)),
        skip_blank=bool(settings.get("skip_blank", True)),
    )
    if out["size"] not in PAPER_SIZES:
        raise ValueError(f"Unknown paper size: {out['size']}")
    return out


def describe(settings: dict) -> str:
    """Short label, e.g. 'Letter · B&W · Double-sided · Blanks removed'."""
    size = "Auto size" if settings["auto_size"] else PAPER_SIZES[settings["size"]][0].split(" (")[0]
    parts = [size, "Color" if settings["color"] else "B&W",
             "Double-sided" if settings["duplex"] else "Single-sided"]
    if settings["skip_blank"]:
        parts.append("Blanks removed")
    return " · ".join(parts)


def dpi(settings: dict) -> int:
    return RES_COLOR if settings["color"] else RES_BW


# Blank-page detection. Real content is concentrated (a line of text, a
# signature), while scanner noise and bleed-through are spread thinly, so the
# page is cut into ~1/3-inch tiles and counts as blank unless at least
# BLANK_MIN_TILES tiles are BLANK_TILE_INK dark. Lower BLANK_TILE_INK if
# sparse pages get dropped; raise it if blank backs with bleed-through are kept.
# `python3 scanner.py --ink page*.png` prints the numbers for tuning.
BLANK_TILE_INK = 0.02     # 2 % of a tile must be ink to count it
BLANK_MIN_TILES = 2       # inked tiles needed for the page to be kept
BLANK_MARGIN = 0.06       # ignore this fraction of each edge (shadows, holes)
DARK_LEVEL = 160          # 0-255; below this counts as ink


class ScanError(Exception):
    pass


def inked_tiles(path: Path, dpi: int) -> tuple[int, float]:
    """Return (number of inked tiles, densest tile's ink fraction)."""
    with Image.open(path) as im:
        g = im.convert("L")
    w, h = g.size
    mx, my = int(w * BLANK_MARGIN), int(h * BLANK_MARGIN)
    g = g.crop((mx, my, w - mx, h - my))
    # Work at half resolution: box averaging turns lone specks light grey so
    # they drop out, while real strokes stay dark.
    g = g.reduce(2)
    ink = g.point(lambda v: 255 if v < DARK_LEVEL else 0)
    tile = max(1, round(dpi / 6))          # 1/3 inch at the halved resolution
    tiles = ink.resize((max(1, ink.width // tile), max(1, ink.height // tile)), Image.BOX)
    hist = tiles.histogram()
    level = round(BLANK_TILE_INK * 255)
    top = max(i for i, n in enumerate(hist) if n) if any(hist) else 0
    return sum(hist[level:]), top / 255


def is_blank(path: Path, dpi: int) -> bool:
    return inked_tiles(path, dpi)[0] < BLANK_MIN_TILES


def remove_blank_pages(job: Path, pages: list[Path], dpi: int) -> list[Path]:
    """Move blank pages into job/blank/ and return the pages that are kept."""
    kept, blank_dir = [], job / "blank"
    for page in pages:
        if is_blank(page, dpi):
            blank_dir.mkdir(exist_ok=True)
            page.rename(blank_dir / page.name)
        else:
            kept.append(page)
    return kept


def scan(settings: dict, job: Path | None = None) -> tuple[Path, list[Path]]:
    """Scan into `job` (a new folder under SCAN_ROOT if not given)."""
    settings = validate(settings)
    job = job or SCAN_ROOT / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    if settings["auto_size"]:
        width, height = AUTO_AREA
    else:
        _, width, height = PAPER_SIZES[settings["size"]]
    cmd = [
        "scanimage", "-d", "fujitsu",
        "--source", "ADF Duplex" if settings["duplex"] else "ADF Front",
        "--mode", "Color" if settings["color"] else "Lineart",
        "--resolution", str(dpi(settings)),
        # Paper size first: it sets the limits for the scan area after it.
        f"--page-width={width}", f"--page-height={height}",
        "-l", "0", "-t", "0", "-x", str(width), "-y", str(height),
        f"--swcrop={'yes' if settings['auto_size'] else 'no'}",
        "--swdeskew=yes",
        "--format=png", f"--batch={job}/page%03d.png",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    pages = sorted(job.glob("page*.png"))
    # scanimage exits non-zero when the feeder runs out at the end of a
    # normal batch, so success is judged by whether pages were produced.
    if not pages:
        shutil.rmtree(job, ignore_errors=True)
        raise ScanError(r.stderr.strip() or "No pages scanned (feeder empty?)")
    if settings["skip_blank"]:
        pages = remove_blank_pages(job, pages, dpi(settings))
        if not pages:
            shutil.rmtree(job, ignore_errors=True)
            raise ScanError("Every page was blank")
    return job, pages


def build_pdf(job: Path, pages: list[Path], settings: dict) -> Path:
    d = dpi(settings)
    layout = img2pdf.get_fixed_dpi_layout_fun((d, d))
    pdf = job / "scan.pdf"
    pdf.write_bytes(img2pdf.convert([str(p) for p in pages], layout_fun=layout))
    return pdf


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Scan once into /tmp/scan (no upload). "
                                 "With no options this does what the Scan button does.")
    ap.add_argument("--size", choices=PAPER_SIZES, default="letter")
    ap.add_argument("--auto-size", action="store_true", help="detect the paper size")
    ap.add_argument("--color", action="store_true")
    ap.add_argument("--single", action="store_true", help="front side only")
    ap.add_argument("--keep-blank", action="store_true", help="don't remove blank pages")
    ap.add_argument("--ink", nargs="+", metavar="PNG",
                    help="only print the blank-page numbers for these images")
    ap.add_argument("--dpi", type=int, default=RES_BW, help="dpi of the --ink images")
    a = ap.parse_args()
    if a.ink:
        for f in a.ink:
            n, top = inked_tiles(Path(f), a.dpi)
            verdict = "BLANK" if n < BLANK_MIN_TILES else "keep"
            print(f"{f}: {n} inked tile(s), densest {top:.1%}  {verdict}")
        return
    settings = validate(dict(size=a.size, auto_size=a.auto_size, color=a.color,
                             duplex=not a.single, skip_blank=not a.keep_blank))
    print(describe(settings))
    job, pages = scan(settings)
    blanks = len(list((job / "blank").glob("*.png")))
    print(f"Scanned {len(pages)} page(s) into {job}"
          + (f" ({blanks} blank page(s) removed)" if blanks else ""))
    print(f"PDF written to {build_pdf(job, pages, settings)}")


if __name__ == "__main__":
    main()
