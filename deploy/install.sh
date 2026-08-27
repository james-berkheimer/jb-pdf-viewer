#!/usr/bin/env bash
# Install jb-pdf-viewer as a systemd service and wire up the shell helpers.
# The service runs as your own user so it keeps the group memberships that
# grant access to the NFS library; sudo is used to install and control the unit.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="/etc/systemd/system"
UNIT="jb-pdf-viewer.service"
ALIASES_LINE="source \"$REPO/deploy/aliases.sh\""

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

say "Checking prerequisites"
[ -x "$REPO/.venv/bin/uvicorn" ] || {
    echo "No virtualenv found. Run first:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
}
echo "  virtualenv    ok"
[ -f "$REPO/data/library.db" ] \
    && echo "  library index ok" \
    || echo "  library index missing - run scripts/index_library.py after this"

say "Checking library access"
# The unit runs as $USER so it inherits the group memberships that grant
# access to the NFS export. Catch a missing one here rather than as a wall of
# 500s once the reader is up.
LIB=$(grep -m1 'Environment=PDFV_LIBRARY=' "$REPO/deploy/$UNIT" \
        | sed 's/^Environment=PDFV_LIBRARY=//')
if sudo -u "$USER" test -r "$LIB" 2>/dev/null; then
    echo "  $USER can read $LIB"
else
    echo "  WARNING: $USER cannot read $LIB"
    echo "  Check group membership, then log out and back in:"
    echo "      id $USER"
fi

say "Installing the service unit"
sudo cp "$REPO/deploy/$UNIT" "$UNIT_DIR/$UNIT"
sudo systemctl daemon-reload
echo "  $UNIT_DIR/$UNIT"

say "Publishing the name over mDNS"
if systemctl list-unit-files avahi-daemon.service >/dev/null 2>&1; then
    sudo install -m 0755 "$REPO/deploy/mdns-alias.sh" /usr/local/bin/jb-pdf-viewer-mdns
    sudo cp "$REPO/deploy/jb-pdf-viewer-mdns.service" "$UNIT_DIR/"
    sudo systemctl daemon-reload
    sudo systemctl enable --now jb-pdf-viewer-mdns.service >/dev/null 2>&1
    echo "  http://jb-pdf-viewer.local"
else
    echo "  avahi-daemon not installed - skipping the name."
    echo "  Install it for http://jb-pdf-viewer.local :"
    echo "      sudo apt install avahi-daemon avahi-utils"
fi

say "Enabling start at boot"
sudo systemctl enable "$UNIT" >/dev/null
echo "  enabled"

say "Installing shell helpers"
touch "$HOME/.bash_aliases"
if grep -Fqx "$ALIASES_LINE" "$HOME/.bash_aliases"; then
    echo "  already sourced from ~/.bash_aliases"
else
    {
        echo ""
        echo "# jb-pdf-viewer helpers (pdfv-help for the list)"
        echo "$ALIASES_LINE"
    } >> "$HOME/.bash_aliases"
    echo "  added to ~/.bash_aliases"
fi

say "Starting"
sudo systemctl restart "$UNIT"
sleep 3
if systemctl is-active --quiet "$UNIT"; then
    if systemctl is-active --quiet jb-pdf-viewer-mdns.service 2>/dev/null; then
        echo "  running on http://jb-pdf-viewer.local"
    else
        echo "  running on http://$(hostname -I | awk '{print $1}')"
    fi
else
    echo "  failed to start. Recent log:"
    journalctl --user -u "$UNIT" -n 20 --no-pager | sed 's/^/      /'
    exit 1
fi

cat <<EOF

Done. Open a new shell (or: source ~/.bash_aliases) then:

    pdfv          status and URL
    pdfv-help     every command

EOF
