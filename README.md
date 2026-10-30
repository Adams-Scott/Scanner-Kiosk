# Scanner Kiosk

A Raspberry Pi service for a Fujitsu ScanSnap S1500. Press the scanner's own
**Scan** button and it scans, drops blank pages, makes a PDF and uploads it to
[Papra](https://papra.app). A small web page, built for a phone, shows what's
happening, lets you retry uploads, and starts scans with other settings.

## Install (Raspberry Pi)

```bash
git clone https://github.com/Adams-Scott/Scanner-Kiosk.git ~/Scanner-Kiosk
cd ~/Scanner-Kiosk
./install.sh
```

Run it as your normal user (it asks for sudo when needed). It installs the system
packages and Python environment, adds you to the `scanner` group, creates the
settings file and starts the `scanner-kiosk` service. Re-running it never
overwrites your settings.

### Settings

Put your Papra details in `/etc/scanner-kiosk.env` (readable by root only), then
restart the service:

```bash
sudo nano /etc/scanner-kiosk.env      # PAPRA_URL, PAPRA_ORG_ID, PAPRA_API_KEY
sudo systemctl restart scanner-kiosk
```

The API key needs the `documents:create` permission. The web page shows whether
Papra accepted the settings; `./install.sh` also checks them. Open the page on your
phone at `http://pi-touch.local:8080` (the installer prints the exact address).

| Task | Command |
| --- | --- |
| Update after `git pull` | `./install.sh` |
| Change the settings | `sudo nano /etc/scanner-kiosk.env`, then restart |
| Remove the service (keeps scans and settings) | `./install.sh --uninstall` |
| Live log | `journalctl -u scanner-kiosk -f` |
| Restart | `sudo systemctl restart scanner-kiosk` |

## How it works

- **Scan button**: `button.py` polls the scanner's `scan` sensor. A press always
  scans Letter (8.5 × 11 in), black & white, double-sided, with blank pages removed
  (`BUTTON_SETTINGS` in `scanner.py`).
- **Scan from the phone**: pick the paper size (or automatic size detection),
  single- or double-sided, color or black & white, and whether to remove blank
  pages. The phone remembers your last choices.
- **Scan**: `scanner.py` runs `scanimage`. Automatic size scans a Legal-size area
  and crops each page to its edges. B&W scans at 300 dpi, color at 200 dpi.
- **Upload**: `kiosk.py` uploads each PDF to Papra, named `Scan YYYY-MM-DD HH-MM-SS.pdf`.
  If Papra can't be reached the upload is retried automatically (after 30 s,
  then up to every 15 min) and survives restarts. If Papra rejects the upload
  (for example a bad API key), it waits for **Retry** on the web page; uploads
  are also retried automatically once a Papra connection check passes.
- **Storage**: scans live in `~/scanner-kiosk-data/jobs/`. Uploaded PDFs are
  kept for 7 days (viewable from the web page), then deleted.

The web page has no login. Keep it on your home network; don't publish it
through a tunnel or port forward without putting authentication in front.

## Testing pieces on their own

Stop the service first (`sudo systemctl stop scanner-kiosk`): only one program
can use the scanner at a time.

```bash
python3 scanner.py               # one scan like the button, into /tmp/scan (no upload)
python3 scanner.py --help        # options: --size, --auto-size, --color, --single, --keep-blank
python3 button.py --debug        # watch the button and sensors
python3 buttontest.py            # compare ways of detecting the button
```

If blank pages are wrongly dropped or kept, check the numbers and adjust
`BLANK_TILE_INK` in `scanner.py`:

```bash
python3 scanner.py --ink /tmp/scan/<job>/page*.png /tmp/scan/<job>/blank/*.png
# color scans are 200 dpi: add --dpi 200
```

"Scanner unavailable ... Device busy" means another program (for example
`scanbd`, `saned` or a second copy of this service) is holding the scanner.
