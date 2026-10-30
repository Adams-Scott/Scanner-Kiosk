#!/usr/bin/env bash
# Install or update the Scanner Kiosk service on a Raspberry Pi.
#
#   ./install.sh                 install, or update after `git pull`
#   ./install.sh --uninstall     stop and remove the service (keeps scans and settings)
#
# Run it as your normal user from the repo folder; it uses sudo where needed.
set -euo pipefail

SERVICE=scanner-kiosk
ENV_FILE=/etc/scanner-kiosk.env
UNIT_FILE=/etc/systemd/system/$SERVICE.service
APP_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RUN_USER=$(id -un)
DATA_DIR=$(getent passwd "$RUN_USER" | cut -d: -f6)/scanner-kiosk-data
PORT=8080
APT_PACKAGES=(sane-utils libsane-dev python3-dev python3-venv build-essential)

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -ne 0 ]] || die "Run this as your normal user (not with sudo); it asks for sudo when needed."
command -v apt-get >/dev/null || die "This script needs a Debian-based system such as Raspberry Pi OS."

uninstall() {
    step "Removing the $SERVICE service"
    sudo systemctl disable --now "$SERVICE" 2>/dev/null || true
    sudo rm -f "$UNIT_FILE"
    sudo systemctl daemon-reload
    echo "Removed. Kept your settings ($ENV_FILE) and scans ($DATA_DIR)."
    exit 0
}

case ${1:-} in
    --uninstall) uninstall ;;
    "") ;;
    *) die "Unknown option: $1 (the only option is --uninstall)" ;;
esac

# 1. System packages ---------------------------------------------------------
step "Installing system packages"
missing=()
for p in "${APT_PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing+=("$p")
done
if ((${#missing[@]})); then
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
else
    echo "Already installed."
fi

# 2. Scanner access ----------------------------------------------------------
step "Checking scanner access"
if getent group scanner >/dev/null && ! id -nG "$RUN_USER" | grep -qw scanner; then
    sudo usermod -aG scanner "$RUN_USER"
    echo "Added $RUN_USER to the scanner group."
fi
if systemctl is-active --quiet scanbd 2>/dev/null; then
    warn "scanbd is running and will hold the scanner. Stopping and disabling it."
    sudo systemctl disable --now scanbd
fi
# Free the scanner for the check below if the service is already running.
sudo systemctl stop "$SERVICE" 2>/dev/null || true
if scanimage -L 2>/dev/null | grep -qi fujitsu; then
    scanimage -L | grep -i fujitsu
else
    warn "The ScanSnap wasn't found. Check it's plugged in with the lid open; the service will keep looking for it."
fi

# 3. Python environment ------------------------------------------------------
step "Setting up Python packages"
[[ -x $APP_DIR/.venv/bin/python ]] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
echo "Done."

# 4. Settings file -----------------------------------------------------------
step "Settings ($ENV_FILE)"
if ! sudo test -e "$ENV_FILE"; then
    sudo install -m 600 -o root -g root "$APP_DIR/scanner-kiosk.env.example" "$ENV_FILE"
    echo "Created it from scanner-kiosk.env.example."
fi
if sudo grep -Eq '^PAPRA_(URL|ORG_ID|API_KEY)=\s*$' "$ENV_FILE"; then
    configured=no
    warn "The Papra settings aren't filled in yet. Scans will wait until they are."
else
    configured=yes
    echo "Checking the connection to Papra..."
    if ! sudo env -i PATH="$PATH" bash -c "set -a; source '$ENV_FILE'; set +a; \
            cd '$APP_DIR' && exec '$APP_DIR/.venv/bin/python' papra.py"; then
        warn "Papra check failed. Scans will wait and upload once the settings work."
        configured=no
    fi
fi

# 5. Service -----------------------------------------------------------------
step "Installing the $SERVICE service"
mkdir -p "$DATA_DIR"
sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=Scanner Kiosk (ScanSnap button -> Papra)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=KIOSK_DATA=$DATA_DIR KIOSK_PORT=$PORT
ExecStart=$APP_DIR/.venv/bin/python kiosk.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --quiet "$SERVICE"
sudo systemctl restart "$SERVICE"

# 6. Check it's up -----------------------------------------------------------
step "Starting"
for _ in $(seq 1 20); do
    if "$APP_DIR/.venv/bin/python" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/status', timeout=2)" 2>/dev/null; then
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
        echo "Running. Open http://$(hostname).local:$PORT${ip:+ (or http://$ip:$PORT)} on your phone."
        echo "Live log: journalctl -u $SERVICE -f"
        if [[ $configured == no ]]; then
            echo
            echo "Next: put your Papra details in the settings file, then restart:"
            echo "  sudo nano $ENV_FILE"
            echo "  sudo systemctl restart $SERVICE"
        fi
        exit 0
    fi
    sleep 1
done
sudo systemctl --no-pager status "$SERVICE" | tail -n 15
die "The service didn't start. See the log above, or: journalctl -u $SERVICE -n 50"
