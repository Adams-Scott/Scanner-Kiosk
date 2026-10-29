# Scanner Kiosk

Raspberry Pi touch-screen kiosk that scans documents with a Fujitsu ScanSnap S1500
(via SANE), lets the user accept or rescan, and uploads the PDF to Papra.

## Setup (Raspberry Pi)

```bash
sudo apt install sane-utils libsane-dev python3-dev build-essential python3-venv
scanimage -L        # should list the ScanSnap S1500
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Test a scan

```bash
python3 scanner.py color_single   # or color_double, bw_single, bw_double
```

Duplex presets (`bw_double`, `color_double`) drop blank pages automatically; the
removed pages are kept in the job's `blank/` folder. If a page is wrongly dropped
or kept, check the numbers and adjust `BLANK_TILE_INK` in `scanner.py`:

```bash
python3 scanner.py --ink /tmp/scan/<job>/page*.png /tmp/scan/<job>/blank/*.png
```

## Scan button

Pressing the scanner's own Scan button runs a black & white, double-sided scan.
To test it on its own:

```bash
scanimage -d fujitsu -A | grep -A1 -i hardware   # the "scan" sensor should be listed
python3 button.py                                # press the button with paper loaded
```

`ButtonWatcher` polls the button while idle and releases the scanner while a scan
runs. Anything else that scans (the touch UI) should do so inside
`with watcher.released():` so the two never use the scanner at the same time.

## Papra credentials

Keep the Papra base URL, organization ID and API key in an environment file on the
Pi (for example `/etc/scanner-kiosk.env`, mode 600). Never commit them.

## Roadmap

- FastAPI service (scan, status, preview, accept, discard); start `ButtonWatcher` in it
  and send button scans to the preview screen
- Touch UI (menu, scanning, preview, done)
- Papra upload with a pending-upload queue
- systemd units and Chromium kiosk mode
