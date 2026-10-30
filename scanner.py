"""Scan module for the Scanner Kiosk (Fujitsu ScanSnap S1500 via SANE)."""
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import img2pdf
from PIL import Image

SCAN_ROOT = Path("/tmp/scan")

# skip_blank: drop pages that come out blank (mostly the empty backs of
# single-sided originals fed through in duplex).
LABELS = {
    "bw_double": "B&W, double-sided",
    "bw_single": "B&W, single-sided",
    "color_double": "Color, double-sided",
    "color_single": "Color, single-sided",
}

PRESETS = {
    "color_single": dict(mode="Color",   res=200, source="ADF Front",  skip_blank=False),
    "color_double": dict(mode="Color",   res=200, source="ADF Duplex", skip_blank=True),
    "bw_single":    dict(mode="Lineart", res=300, source="ADF Front",  skip_blank=False),
    "bw_double":    dict(mode="Lineart", res=300, source="ADF Duplex", skip_blank=True),
}

# Software crop/deskew. Remove any option that `scanimage -d fujitsu --help`
# does not list for your SANE version.
EXTRA_OPTS = ["--swcrop=yes", "--swdeskew=yes"]


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


def scan(preset: str, job: Path | None = None) -> tuple[Path, list[Path]]:
    """Scan into `job` (a new folder under SCAN_ROOT if not given)."""
    p = PRESETS[preset]
    job = job or SCAN_ROOT / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=True)
    cmd = [
        "scanimage", "-d", "fujitsu",
        "--source", p["source"], "--mode", p["mode"],
        "--resolution", str(p["res"]),
        *EXTRA_OPTS,
        "--format=png", f"--batch={job}/page%03d.png",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    pages = sorted(job.glob("page*.png"))
    # scanimage exits non-zero when the feeder runs out at the end of a
    # normal batch, so success is judged by whether pages were produced.
    if not pages:
        shutil.rmtree(job, ignore_errors=True)
        raise ScanError(r.stderr.strip() or "No pages scanned (feeder empty?)")
    if p["skip_blank"]:
        pages = remove_blank_pages(job, pages, p["res"])
        if not pages:
            shutil.rmtree(job, ignore_errors=True)
            raise ScanError("Every page was blank")
    return job, pages


def build_pdf(job: Path, pages: list[Path], preset: str) -> Path:
    dpi = PRESETS[preset]["res"]
    layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    pdf = job / "scan.pdf"
    pdf.write_bytes(img2pdf.convert([str(p) for p in pages], layout_fun=layout))
    return pdf


if __name__ == "__main__":
    if sys.argv[1:2] == ["--ink"]:   # --ink [--dpi N] page.png ...
        args, dpi = sys.argv[2:], 300
        if args[:1] == ["--dpi"]:
            dpi, args = int(args[1]), args[2:]
        for f in args:
            n, top = inked_tiles(Path(f), dpi)
            verdict = "BLANK" if n < BLANK_MIN_TILES else "keep"
            print(f"{f}: {n} inked tile(s), densest {top:.1%}  {verdict}")
        sys.exit()
    preset = sys.argv[1] if len(sys.argv) > 1 else "color_single"
    job, pages = scan(preset)
    blanks = len(list((job / "blank").glob("*.png")))
    print(f"Scanned {len(pages)} page(s) into {job}"
          + (f" ({blanks} blank page(s) removed)" if blanks else ""))
    print(f"PDF written to {build_pdf(job, pages, preset)}")
