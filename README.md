# Meshsites

Serve tiny sites over [Meshtastic](https://meshtastic.org) LoRa radios —
Gopher-over-LoRa: read-mostly, slow, local by physics. This is a Python
server implementing **Meshsites protocol v1 (draft 7)**: beaconed discovery,
one-packet requests, DEFLATE-compressed chunked pages on port 421, all
strictly `hop_limit = 1` so the mesh never relays a byte of it.

A site is a directory: Markdown-ish (**Meshdown**) files for static pages,
plain Python files for dynamic ones. A weekend-project protocol by design —
the whole wire format fits on one page of the spec.

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

The server beacons the site name on startup and every 5 minutes ± 30 s;
nearby Meshsites clients list it and can browse. Send `SIGUSR1` to the
server process (the PID is in the startup log) to fire a beacon immediately.

```sh
meshsites serve mysite \
    --device /dev/serial/by-id/usb-RAKwireless_WisCore_RAK4631_...-if00 \
    --log-file /var/log/meshsites.log
```

Prefer a `/dev/serial/by-id/...` path over `/dev/ttyACM0` — ACM numbering
follows USB enumeration order and can silently swap devices after a replug.

## Site directories

Requests map to files:

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

## Logging

One access-log line per request (sender, method, path, outcome, duration),
including rejected, malformed, and BUSY requests. Beacons from other
Meshsites servers and plain text messages heard by the node are logged at
INFO too, so the log doubles as a view of who's around. `--log-file PATH`
appends everything to a file; `-v` adds debug detail.

```
20:16:01 INFO meshsites.server: beacon sent ('Granges Base')
20:32:54 INFO meshsites.access: beacon heard from !8ac3c723: site 'W2ASM' (v1)
20:35:12 INFO meshsites.access: !8ac3c723 GET / -> 1 chunk, 74 bytes deflated in 1.2s
```

## Protocol compliance notes

- All frames sent with `hop_limit = 1` on the primary channel; received
  frames that were relayed (`hop_start > 0 && hop_limit < hop_start`) are
  dropped, as are frames over 200 bytes. Unicast frames are accepted under
  channel or PKI encryption (spec draft 8) — decryption is the radio's job.
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

## Radio requirements

- `device.rebroadcast_mode` MUST NOT be `CORE_PORTNUMS_ONLY` on either end
  — it silently drops non-core portnums (like Meshsites' 421) on receive.
  Use `ALL` (the default).
- Both radios need matching primary-channel settings (preset, slot,
  name/PSK); PKI DMs working between two nodes does *not* prove the
  channel layer matches — beacons are channel-encrypted broadcasts.

## Running as a service

```ini
# /etc/systemd/system/meshsites.service
[Unit]
Description=Meshsites server
After=multi-user.target

[Service]
ExecStart=/usr/local/bin/meshsites serve /srv/mysite --log-file /var/log/meshsites.log
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

`TODO.md` tracks the running feature list. The protocol spec (v1 draft 7)
defines the wire format; `meshsites/protocol.py` is a direct transcription
of it and the test suite pins the edge cases (error-code precedence, POST
etag normalization, chunk sizing, relay-discard).
