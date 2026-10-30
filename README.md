# Scanner Kiosk

A Raspberry Pi service for a Fujitsu ScanSnap S1500. Press the scanner's own
**Scan** button and it scans, drops blank pages, makes a PDF and uploads it to
[Papra](https://papra.app). A small web page, built for a phone, shows what's
happening and lets you retry uploads, scan in other modes, and choose what the
button does.

## Install (Raspberry Pi)

```bash
git clone https://github.com/Adams-Scott/Scanner-Kiosk.git ~/Scanner-Kiosk
cd ~/Scanner-Kiosk
./install.sh
```

Run it as your normal user (it asks for sudo when needed). It:

1. installs the system packages (`sane-utils` and what's needed to build `python-sane`),
2. adds you to the `scanner` group and checks the ScanSnap is visible,
3. creates a Python environment in `.venv` and installs `requirements.txt`,
4. asks for your Papra address, organization ID and API key, saves them to
   `/etc/scanner-kiosk.env` (readable by root only) and checks them,
5. installs and starts the `scanner-kiosk` systemd service.

It then prints the address to open on your phone, e.g. `http://pi-touch.local:8080`.
The API key needs the `documents:create` permission.

| Task | Command |
| --- | --- |
| Update after `git pull` | `./install.sh` |
| Change the Papra settings | `./install.sh --reconfigure` |
| Remove the service (keeps scans and settings) | `./install.sh --uninstall` |
| Live log | `journalctl -u scanner-kiosk -f` |
| Restart | `sudo systemctl restart scanner-kiosk` |

## How it works

- **Scan button**: `button.py` polls the scanner's `scan` sensor. A press runs the
  mode chosen on the web page (black & white double-sided by default).
- **Scan**: `scanner.py` runs `scanimage`. Double-sided modes drop blank pages.
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
