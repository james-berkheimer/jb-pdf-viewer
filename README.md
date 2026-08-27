# jb-pdf-viewer

A self-hosted web reader for PDF collections. Point it at any folders you like;
it indexes them, renders pages server-side, and gives you a reader built for
long scanned documents.

The point is to beat a browser's built-in PDF plugin: two-page spreads,
article-level contents, a magnifier for small scanned type, and search that
spans every document at once.

## How it works

These are image-heavy scans, so pages are **rasterised on the server** and sent
to the browser as WebP, rather than shipping a 6–100 MB PDF to the client and
making it do the work.

```
browser ──GET /api/doc/dragon-76/page/8?w=1400──► FastAPI
                                                    │
                                            cache hit? ──► WebP ~420 KB
                                                    │ miss
                                            PyMuPDF render (11 ms)
                                            Pillow WebP encode (114 ms)
                                                    └──► disk cache ──► WebP
```

A page turn costs one cached image. Neighbouring pages are prefetched while you
read, so forward navigation is usually already warm. Selectable text is a
separate overlay of word boxes positioned over the image, which is also what
makes search hits highlight in place on the scan.

**Why not PDF.js?** For scanned magazines it renders slowly on first paint and
is heavy on phones and tablets. Server-side rendering makes the client's job
trivial; the tradeoff is cache disk, which is capped and evicted LRU.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh                                    # http://0.0.0.0:8800
```

For a permanent install see **Run as a service** below, which serves it on
port 80 under the host's own name.

`run.sh` builds the index automatically on first start.

## Libraries

A library is a name, a folder, and a profile saying how to group what is
inside. Nothing is hard-coded — add as many as you like.

### From the browser

The **Libraries** button in the top bar opens a manager: browse the server's
folders (it shows how many PDFs are under each one), pick a root, and add and
scan it with live progress. From there you can also rescan, change a library's
grouping, hide it, or remove it. Removing a library never deletes files.

The manager is backed by `/api/admin/*`. Those endpoints can add libraries and
list server directories, which is fine on a private LAN and unwise on the open
internet — set `PDFV_ADMIN=0` to switch the whole admin API off (the reader is
unaffected), or `PDFV_BROWSE_ROOTS` to restrict what the folder browser can
see.

### From the command line

```bash
scripts/library.py add "Pathfinder" /mnt/media/books/roleplaying/pathfinder --index
scripts/library.py add "Comics"     /mnt/media/books/comics --profile folders --index
scripts/library.py list
scripts/library.py set pathfinder --name "Pathfinder 1e" --disable
scripts/library.py remove pathfinder
```

`--index` scans straight away; without it, run
`scripts/index_library.py --library <id>` when you are ready.

They live in `data/libraries.json`, so you can also just edit that file and
re-run the indexer. Removing a library never touches your files.

### Profiles

| Profile | Grouping |
|---|---|
| `auto` (default) | samples the filenames and picks `magazine` or `folders` |
| `magazine` | filenames encode a series and issue number (`Dragon #141`) |
| `folders` | the containing folder is the group; deeper folders show as a breadcrumb |
| `flat` | one group for everything |

`auto` only chooses `magazine` when at least 60% of a library's filenames end
in an issue number, so a folder of rulebooks will not be mangled into a
pretend magazine run.

Re-running the indexer only reprocesses files whose size or mtime changed, so
adding a library never re-scans the others. Document ids are derived from the
library plus the file's path, which is what lets bookmarks survive a reindex.

### Configuration

All via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `PDFV_LIBRARY` | the D&D magazines path | Seeds `libraries.json` on first run only |
| `PDFV_DATA` | `./data` | Index, covers, page cache and `libraries.json` |
| `PDFV_CACHE_GB` | `20` | Page-cache ceiling; LRU beyond it |
| `PDFV_WORKERS` | CPUs − 1 | Render/index parallelism |
| `PDFV_HOST` / `PDFV_PORT` | `0.0.0.0` / `8800` | Bind address (`run.sh` only) |
| `PDFV_ADMIN` | `1` | Set `0` to disable the library-manager API |
| `PDFV_BROWSE_ROOTS` | unset | `:`-separated folders the browser may list |

### Run as a service

```bash
deploy/install.sh
```

Installs the systemd unit, enables it at boot, starts it, and adds the shell
helpers to `~/.bash_aliases`. Safe to re-run.

Then, from any SSH session:

| | |
|---|---|
| `pdfv` | is it up, on what URL, and what it holds |
| `pdfv-start` `pdfv-stop` `pdfv-restart` | control it |
| `pdfv-status` | full systemd status |
| `pdfv-logs` | follow the log |
| `pdfv-errors` | recent warnings and errors |
| `pdfv-libs list` | configured libraries |
| `pdfv-libs add "Name" /path --index` | add one and scan it |
| `pdfv-scan [id]` | rescan everything, or one library |
| `pdfv-warm --pages 6` | pre-render opening pages |
| `pdfv-update` | git pull, deps, reinstall unit if changed, restart |
| `pdfv-disk` | what `data/` is using |
| `pdfv-help` | the whole list |

### Reaching it by name

The service binds port 80 directly via `AmbientCapabilities=CAP_NET_BIND_SERVICE`,
so no reverse proxy is needed just to drop `:8800` from the URL. For the name
itself, `avahi-daemon` publishes the host over mDNS:

    http://aule.local

That resolves from macOS, iOS/iPadOS, Linux, Windows 10+ and Android 12+
without touching the router. Avahi is pinned to the LAN interface
(`allow-interfaces=eno1` in `/etc/avahi/avahi-daemon.conf`) because it
otherwise also advertises the Docker bridge address, which clients cannot
reach.

If you would rather have a name of your own choosing — `books.lan`,
`library.home` — add a static DNS entry on the router pointing it at this
host; nothing in the app needs to change.

The unit runs read-only against the library and only writes `./data`. It waits
for the NFS mount before starting, restarts on failure, and runs at `Nice=5`
with a reduced `CPUWeight` so it yields to anything else on the box rather than
competing for the CPU during a render burst.

**Why a system unit and not a `--user` one.** The library is an NFS export
whose directories are mode 770, reachable only through supplementary group
membership. A `systemd --user` manager fixes its group list at login, so any
group added afterwards is invisible to it and every PDF read fails with
`EACCES`. A system unit with `User=` runs `initgroups()` at each start and
always has the current set. If you ever hit permission errors on the library,
compare `id` against the service's groups:

```bash
sudo tr '\0' '\n' < /proc/$(systemctl show jb-pdf-viewer -p MainPID --value)/status \
    | grep Groups
```

### Optional: pre-warm the cache

```bash
.venv/bin/python scripts/prewarm.py --pages 6      # opening pages, ~1.4 GB
.venv/bin/python scripts/prewarm.py --all-pages    # everything, ~22 GB
```

## Reading

| Key | |
|---|---|
| `←` `→` `Space` | Previous / next page |
| `1` `2` `3` | Single / spread / continuous scroll |
| `+` `−` `0` | Zoom in, out, cycle fit page ↔ fit width |
| `z` | Magnifier — hover to inspect, click to zoom in and read |
| `t` | Sidebar · `/` Find in document · `g` Go to page |
| `o` | Offset spread pairing by one |
| `b` | Reading surface: dark / sepia / light |
| `[` `]` | Previous / next document in the group |
| `f` | Fullscreen · `?` Shortcuts |

**Every one of these can be changed.** Press `?` (or the `?` button) to open
the shortcuts sheet: click a key to reassign it, `+` to add a second key to an
action, `×` to drop one. A key can only drive one action, so binding a key
already in use takes it from the other action and says so. **Reset** restores
the defaults. Bindings are stored per browser, and only the ones you changed
are saved — so defaults can improve later without wiping your customisations.
`Esc` is reserved and cannot be rebound, so there is always a way to cancel.

Spreads pair the way a real magazine does — the cover sits alone, then 2–3,
4–5. When a scan includes the inside cover and the pairing looks off by one,
`o` fixes it.

Reading position is saved per document and surfaces as "Continue reading" on
the library page.

### The magnifier

Scanned type is often too small to read at fit-page size. `m` (or the
magnifying-glass button) arms a tool that does two things:

- **Hover** shows a loupe that follows the pointer — good for a map detail or
  a stat block. The wheel changes magnification between 1.5× and 6×, starting
  at 1.5×.
- **Click** zooms the whole page to that magnification, centred on the point
  you clicked, and leaves you there to read and pan. `0` returns to fit.

Both source a 3200px render of the page rather than upscaling the image already
on screen, so magnified text is genuinely sharper rather than bigger and
blurrier. That render is fetched once per page and cached.

While the magnifier is on, a chip in the bottom-right corner says so and
switches it back off, so the mode cannot be entered without a visible way out.
Once the page is larger than the viewport the cursor becomes a grab hand and
dragging pans; the OCR text layer stops taking pointer events while panning, so
the hand does not flicker to an I-beam over every word (text selection works
normally at fit size). The click-to-turn zones along the page edges
hide themselves whenever the page is bigger than the viewport, since they live
inside the scroller and would otherwise drift across the page as you pan; turn
pages with the keyboard, the footer or the scrubber while zoomed in.

## Search

Two FTS5 indexes are queried together: OCR page text, and TOC entries (real
article titles, which rank above body matches — searching `beholder` puts
*"The ecology of the beholder"* first). Results can be filtered to one library.
Typical query is 15–230 ms depending on how common the term is.

**Coverage caveat:** only documents with an OCR text layer can be searched.
The UI labels the others "no OCR" rather than silently returning nothing, and
the library page reports the count. Running tesseract over them and writing the
text back into `pages_fts` is the obvious next step.

## Layout

```
app/
  config.py    settings, cacheable render widths
  libraries.py the library registry (data/libraries.json)
  jobs.py      one-at-a-time background scan with pollable progress
  naming.py    path -> group + title, per profile
  db.py        SQLite schema; FTS5 for pages and TOC; migrations
  indexer.py   per-library walk, text/TOC extraction, covers (process pool)
  render.py    page -> WebP, disk cache, LRU sweep, prefetch
  search.py    FTS5 query building and ranking
  main.py      FastAPI routes
web/           no build step — plain ES modules and CSS
  static/js/magnifier.js   loupe + click-to-zoom
  static/js/textlayer.js   OCR word boxes over the page image
  static/js/libadmin.js    the Libraries manager
  static/js/keymap.js      key bindings: defaults + user overrides
  static/js/shortcuts.js   the editable shortcuts sheet
scripts/       library.py, index_library.py, prewarm.py
```

## Notes on the D&D magazine collection

- 577 PDFs, 53,799 pages, 10 GB; all parse cleanly.
- Filenames use seven conventions (`Accessory - Dragon Magazine #141.pdf`,
  `Dragon #400.pdf`, `Accessory - Strategic Review #1.2.pdf`, …) — normalised
  in `naming.py`.
- Issue numbering has real gaps (Dragon 336–360, Dungeon 129–155): the
  DDI-era online-only issues. Not an indexing bug.
- `Dungeon #202` and other 4E-era issues are landscape; the reader fits to
  height automatically.
