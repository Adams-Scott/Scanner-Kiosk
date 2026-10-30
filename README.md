# Scanner Kiosk

A Raspberry Pi service for a Fujitsu ScanSnap S1500. Press the scanner's own
**Scan** button and it scans, drops blank pages, makes a PDF and uploads it to
[Papra](https://papra.app). A small web page, built for a phone, shows what's
happening and lets you retry uploads, scan in other modes, and choose what the
button does.

## Run with Docker (recommended)

Needs Docker with the compose plugin on the Pi, and a 64-bit OS (`uname -m`
should print `aarch64`).

```bash
cd ~/Scanner-Kiosk
cp scanner-kiosk.env.example scanner-kiosk.env
nano scanner-kiosk.env          # PAPRA_URL, PAPRA_ORG_ID, PAPRA_API_KEY
chmod 600 scanner-kiosk.env
docker compose up -d --build    # first build takes a few minutes on a Pi
docker compose logs -f          # live log
```

Open `http://pi-touch.local:8080` (or the Pi's IP address) on your phone.

- The API key needs the `documents:create` permission. `scanner-kiosk.env` is
  git-ignored; never commit it.
- Scans and uploads waiting to be sent are kept in `./data`.
- Set your time zone in `compose.yaml` (`TZ`); scan names use it.
- After `git pull`, run `docker compose up -d --build` again.
- Nothing else on the Pi may use the scanner while the container runs: stop the
  systemd service below if you installed it, and don't run `scanbd` or `saned`.

## Run without Docker (systemd)

```bash
sudo apt install sane-utils libsane-dev python3-dev build-essential python3-venv
scanimage -L        # should list the ScanSnap S1500
cd ~/Scanner-Kiosk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

sudo cp scanner-kiosk.env.example /etc/scanner-kiosk.env
sudo nano /etc/scanner-kiosk.env      # PAPRA_URL, PAPRA_ORG_ID, PAPRA_API_KEY
sudo chmod 600 /etc/scanner-kiosk.env

sudo cp scanner-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scanner-kiosk
journalctl -u scanner-kiosk -f        # live log
```

The service file assumes user `scott` and the repo at `~/Scanner-Kiosk`; edit it if
either differs. To run it in the foreground instead:
`set -a; source <(sudo cat /etc/scanner-kiosk.env); set +a; python3 kiosk.py`.

## How it works

- **Scan button**: `button.py` polls the scanner's `scan` sensor. A press runs the
  mode chosen on the web page (black & white double-sided by default).
- **Scan**: `scanner.py` runs `scanimage`. Double-sided modes drop blank pages.
- **Upload**: `kiosk.py` uploads each PDF to Papra, named `Scan YYYY-MM-DD HH-MM-SS.pdf`.
  If Papra can't be reached the upload is retried automatically (after 30 s,
  then up to every 15 min) and survives restarts. If Papra rejects the upload
  (for example a bad API key), it waits for **Retry** on the web page; uploads
  are also retried automatically once a Papra connection check passes.
- **Storage**: scans live in `./data/jobs/` (Docker) or `~/scanner-kiosk-data/jobs/`. Uploaded PDFs are
  kept for 7 days (viewable from the web page), then deleted.

The web page has no login. Keep it on your home network; don't publish it
through a tunnel or port forward without putting authentication in front.

## Testing pieces on their own

Stop the service first (`docker compose stop` or `sudo systemctl stop scanner-kiosk`):
only one program can use the scanner at a time.

```bash
python3 scanner.py bw_double     # one scan into /tmp/scan (no upload)
python3 button.py --debug        # watch the button and sensors
python3 buttontest.py            # compare ways of detecting the button
```

If blank pages are wrongly dropped or kept, check the numbers and adjust
`BLANK_TILE_INK` in `scanner.py`:

```bash
python3 scanner.py --ink /tmp/scan/<job>/page*.png /tmp/scan/<job>/blank/*.png
```

"Scanner unavailable ... Device busy" means another program (for example
`scanbd`, `saned` or a second copy of this service) is holding the scanner.
