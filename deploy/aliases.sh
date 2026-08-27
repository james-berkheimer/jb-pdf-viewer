# jb-pdf-viewer shell helpers.  Sourced from ~/.bash_aliases.
# Everything runs as your own user - no sudo anywhere.

export PDFV_HOME="${PDFV_HOME:-$HOME/code/jb-pdf-viewer}"
PDFV_UNIT="jb-pdf-viewer.service"
# A system unit (see the unit file for why), so control needs sudo. Reading the
# journal does not - the adm group covers that.
PDFV_CTL="sudo systemctl"
PDFV_PY="$PDFV_HOME/.venv/bin/python"

# --- service control -------------------------------------------------------
alias pdfv-start='sudo systemctl start   '"$PDFV_UNIT"' && sleep 2 && pdfv'
alias pdfv-stop='sudo systemctl stop      '"$PDFV_UNIT"' && echo "  stopped"'
alias pdfv-restart='sudo systemctl restart '"$PDFV_UNIT"' && sleep 2 && pdfv'
alias pdfv-enable='sudo systemctl enable   '"$PDFV_UNIT"' && echo "  will start at boot"'
alias pdfv-disable='sudo systemctl disable  '"$PDFV_UNIT"' && echo "  will not start at boot"'
alias pdfv-status='systemctl status '"$PDFV_UNIT"' --no-pager'
alias pdfv-logs='journalctl -u '"$PDFV_UNIT"' -f -n 40'
alias pdfv-errors='journalctl -u '"$PDFV_UNIT"' -p warning -n 50 --no-pager'
alias pdfv-name='systemctl status jb-pdf-viewer-mdns --no-pager | head -4; avahi-resolve -n jb-pdf-viewer.local'
alias pdfv-cd='cd "$PDFV_HOME"'
alias pdfv-edit='${EDITOR:-nano} "$PDFV_HOME/deploy/jb-pdf-viewer.service"'

# --- library management ----------------------------------------------------
alias pdfv-libs='"$PDFV_PY" "$PDFV_HOME/scripts/library.py"'
alias pdfv-index='"$PDFV_PY" "$PDFV_HOME/scripts/index_library.py"'
alias pdfv-warm='"$PDFV_PY" "$PDFV_HOME/scripts/prewarm.py"'

# --- status ----------------------------------------------------------------
# One-line health check: is it up, on which URL, and what does it hold.
pdfv() {
    local state url port host
    state=$(systemctl is-active "$PDFV_UNIT" 2>/dev/null)
    # Read the port from the unit so this never drifts from what is deployed.
    port=$(systemctl show "$PDFV_UNIT" -p Environment --value 2>/dev/null \
           | tr ' ' '\n' | sed -n 's/^PDFV_PORT=//p')
    # The mDNS alias published by jb-pdf-viewer-mdns.service; fall back to
    # the host's own name if that service is not running.
    host="jb-pdf-viewer.local"
    systemctl is-active --quiet jb-pdf-viewer-mdns.service 2>/dev/null \
        || host="$(hostname).local"
    if [ "${port:-80}" = "80" ]; then url="http://${host}"
    else url="http://${host}:${port}"; fi

    if [ "$state" != "active" ]; then
        printf '  jb-pdf-viewer: \033[31m%s\033[0m   (pdfv-start to bring it up)\n' "$state"
        return 1
    fi
    printf '  jb-pdf-viewer: \033[32mrunning\033[0m   %s\n' "$url"
    curl -s --max-time 4 "$url/api/stats" 2>/dev/null \
        | "$PDFV_PY" "$PDFV_HOME/scripts/status.py"
}

# Update from git and restart. Stops first so the reindex is not racing a
# running server for the SQLite write lock.
pdfv-update() {
    ( cd "$PDFV_HOME" || return 1
      echo "==> pulling"
      git pull --ff-only || { echo "pull failed - resolve by hand"; return 1; }
      echo "==> dependencies"
      "$PDFV_HOME/.venv/bin/pip" install -q -r requirements.txt
      if ! diff -q deploy/jb-pdf-viewer.service \
              /etc/systemd/system/jb-pdf-viewer.service >/dev/null 2>&1; then
          echo "==> unit changed, reinstalling"
          sudo cp deploy/jb-pdf-viewer.service /etc/systemd/system/
          sudo systemctl daemon-reload
      fi
      echo "==> restarting"
      sudo systemctl restart jb-pdf-viewer.service )
    pdfv
}

# Rescan every library, or one by name:  pdfv-scan pathfinder
pdfv-scan() {
    if [ -n "$1" ]; then
        "$PDFV_PY" "$PDFV_HOME/scripts/index_library.py" --library "$1"
    else
        "$PDFV_PY" "$PDFV_HOME/scripts/index_library.py"
    fi
}

# Disk used by the index, covers and rendered pages.
pdfv-disk() {
    du -sh "$PDFV_HOME/data"/* 2>/dev/null | sort -h | sed 's/^/  /'
    printf '  %-10s %s\n' "TOTAL" "$(du -sh "$PDFV_HOME/data" 2>/dev/null | cut -f1)"
}

pdfv-help() {
    cat <<'HELP'
  jb-pdf-viewer

  service     pdfv              status, URL and what it holds
              pdfv-start        start now
              pdfv-stop         stop
              pdfv-restart      restart
              pdfv-status       full systemd status
              pdfv-logs         follow the log (Ctrl-C to leave)
              pdfv-errors       recent warnings and errors
              pdfv-enable       start at boot   (pdfv-disable to undo)

  libraries   pdfv-libs list                      what is configured
              pdfv-libs add "Name" /path --index  add one and scan it
              pdfv-libs remove <id>               forget one (keeps files)
              pdfv-scan [id]                      rescan all, or just one
              pdfv-warm --pages 6                 pre-render opening pages

  upkeep      pdfv-update       git pull, deps, restart
              pdfv-disk         what data/ is using
              pdfv-cd           cd to the repo
HELP
}
