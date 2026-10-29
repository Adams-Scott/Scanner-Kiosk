"""Scan module for the Scanner Kiosk (Fujitsu ScanSnap S1500 via SANE)."""
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import img2pdf

SCAN_ROOT = Path("/tmp/scan")

PRESETS = {
    "color_single": dict(mode="Color",   res=200, source="ADF Front"),
    "color_double": dict(mode="Color",   res=200, source="ADF Duplex"),
    "bw_single":    dict(mode="Lineart", res=300, source="ADF Front"),
    "bw_double":    dict(mode="Lineart", res=300, source="ADF Duplex"),
}

# Software crop/deskew. Remove any option that `scanimage -d fujitsu --help`
# does not list for your SANE version.
EXTRA_OPTS = ["--swcrop=yes", "--swdeskew=yes"]


class ScanError(Exception):
    pass


def scan(preset: str) -> tuple[Path, list[Path]]:
    p = PRESETS[preset]
    job = SCAN_ROOT / uuid.uuid4().hex
    job.mkdir(parents=True)
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
    return job, pages


def build_pdf(job: Path, pages: list[Path], preset: str) -> Path:
    dpi = PRESETS[preset]["res"]
    layout = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    pdf = job / "scan.pdf"
    pdf.write_bytes(img2pdf.convert([str(p) for p in pages], layout_fun=layout))
    return pdf


if __name__ == "__main__":
    preset = sys.argv[1] if len(sys.argv) > 1 else "color_single"
    job, pages = scan(preset)
    print(f"Scanned {len(pages)} page(s) into {job}")
    print(f"PDF written to {build_pdf(job, pages, preset)}")
