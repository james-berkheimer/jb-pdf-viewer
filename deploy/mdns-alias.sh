#!/usr/bin/env bash
# Publish an mDNS alias so the reader answers to the app's own name:
#     http://jb-pdf-viewer.local
#
# Why a publisher process and not an /etc/avahi/hosts entry: a static host
# entry also publishes a reverse PTR record for the address, which collides
# with the one the host already owns for its real name and is rejected with
# "Local name collision". avahi-publish -R adds the forward record only.
#
# The address is resolved at start rather than hardcoded, so a DHCP lease
# change does not leave the name pointing at the wrong host.
set -euo pipefail

NAME="${PDFV_MDNS_NAME:-jb-pdf-viewer.local}"

# Source address of the default route: the LAN interface, never the Docker
# bridge, and correct even if the interface is renamed.
ADDR=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')
[ -n "${ADDR:-}" ] || { echo "could not determine a LAN address" >&2; exit 1; }

echo "publishing ${NAME} -> ${ADDR}"
exec avahi-publish -a -R "$NAME" "$ADDR"
