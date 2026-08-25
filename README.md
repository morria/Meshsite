# Meshsites

Serve tiny sites over [Meshtastic](https://meshtastic.org) LoRa radios —
Gopher-over-LoRa: read-mostly, slow, local by physics. This is a Python
server implementing **Meshsites protocol v1 (draft 7)**: beaconed discovery,
one-packet requests, DEFLATE-compressed chunked pages on port 421, all
strictly `hop_limit = 1` so the mesh never relays a byte of it.

## Install

```sh
pip install .            # from a checkout; needs Python >= 3.11
```

Requires a Meshtastic node attached over USB serial (autodetected) or
reachable over TCP.

## Quick start

```sh
meshsites init mysite --name "My Site"   # scaffold index.md, about.md, guestbook.py
meshsites serve mysite                   # connect to the radio and start serving
```

The server beacons the site name on startup and every 5 minutes; nearby
Meshsites clients list it and can browse.

## Site directories

A site is a directory of pages. Requests map to files:

| Request | File served |
|---|---|
| `/` | `index.py`, else `index.md` |
| `/foo` | `foo.py`, else `foo.md` |
| `/a/b` | `a/b.py`, else `a/b.md` |

`meshsite.toml` in the site directory holds the site name
(`name = "My Site"`); `--name` overrides it.

### Static pages (`.md`)

Meshdown, a line-oriented format: `# / ## / ###` headings, `* item` lists,
`=> /path Label` links, `---` rules, `[form]`/`[field]`/`[submit]` forms,
plain lines as paragraphs. Keep pages small — the compressed hard cap is
3 040 bytes (about 8–10 KB of text).

### Dynamic pages (`.py`)

A Python file that defines `handle(req)` and returns a Meshdown `str`.
Handlers are reloaded automatically when the file changes.

```python
from meshsites import Store, sanitize, NotFound, PageError

def handle(req):
    # req.method     "GET" or "POST"
    # req.path       decoded path, e.g. "/guestbook"
    # req.query      dict of ?key=value pairs
    # req.form       dict of POST body pairs
    # req.args       query and form merged (form wins)
    # req.sender     node id of the requester, e.g. "!a1b2c3d4"
    # req.sender_num numeric node id
    name = sanitize(req.args.get("name", "stranger"))
    return f"# Hello {name}\n\n=> / Home\n"
```

Raise `NotFound()`, `BadRequest("...")`, or `PageError(code, "message")`
to answer with a protocol ERROR instead of a page.

`meshsites.Store` is a persistence helper matching the spec's storage rules:
it strips newlines/control characters from everything stored and bounds the
file to 128 KB, dropping oldest entries. `sanitize()` does the same stripping
for anything user-supplied you render into a page (it also prevents Meshdown
line injection).

## Protocol compliance notes

- All frames sent with `hop_limit = 1` on the primary channel; received
  frames that were relayed (`hop_start > 0 && hop_limit < hop_start`) are
  dropped, as are PKI-encrypted frames and frames over 200 bytes.
- Responses are raw DEFLATE (RFC 1951), max 16 chunks x 190 bytes; pages
  that compress larger answer `ERROR 2 TOO_LARGE`.
- Etags are FNV-1a 32 over the uncompressed page; matching GETs answer
  `NOT_MODIFIED` (2 packets instead of up to 17).
- Chunks are paced: chunk *n+1* goes out when *n* is acked or after 8 s;
  a NAK aborts the response (the 2-minute per-`(requester, id)` response
  cache serves the retry).
- One in-flight response per requester (`ERROR 5 BUSY` otherwise);
  duplicate retransmits of the in-flight request are dropped silently.
- Malformed requests answer `ERROR 3` when the request id is parseable,
  version 0 answers `ERROR 6`; both cases otherwise drop silently.
- Path traversal is rejected after percent-decoding; hidden files
  (dot-prefixed) and anything outside the site directory are never served.

## Running as a service

```ini
# /etc/systemd/system/meshsites.service
[Unit]
Description=Meshsites server
After=dev-ttyACM0.device

[Service]
ExecStart=/usr/local/bin/meshsites serve /srv/mysite
Restart=on-failure
User=meshsites
Group=dialout

[Install]
WantedBy=multi-user.target
```

## Development

```sh
python3 -m unittest discover -s tests -v
```
