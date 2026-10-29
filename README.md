# Scanner Kiosk

Raspberry Pi touch-screen kiosk that scans documents with a Fujitsu ScanSnap S1500
(via SANE), lets the user accept or rescan, and uploads the PDF to Papra.

## Setup (Raspberry Pi)

```bash
sudo apt install sane-utils python3-venv
scanimage -L        # should list the ScanSnap S1500
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Test a scan

```bash
python3 scanner.py color_single   # or color_double, bw_single, bw_double
```

## Papra credentials

Keep the Papra base URL, organization ID and API key in an environment file on the
Pi (for example `/etc/scanner-kiosk.env`, mode 600). Never commit them.

## Roadmap

- FastAPI service (scan, status, preview, accept, discard)
- Touch UI (menu, scanning, preview, done)
- Papra upload with a pending-upload queue
- systemd units and Chromium kiosk mode
