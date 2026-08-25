"""meshsites command line: serve a site directory, or scaffold a new one."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tomllib
from pathlib import Path

from . import __version__
from .protocol import MAX_NAME_BYTES, clean_text
from .server import MeshsiteServer
from .site import Site

log = logging.getLogger("meshsites")

SCAFFOLD = {
    "meshsite.toml": '# Meshsites site configuration\nname = "{name}"\n',
    "index.md": """\
# {name}

Welcome to my meshsite.

* Served over LoRa, direct RF only
* No internet involved

=> /about About this site
=> /guestbook Sign the guestbook
""",
    "about.md": """\
# About

This site is served by the `meshsites` Python service through a
Meshtastic node. Pages are Meshdown: tiny, line-oriented, and cached
by content hash so revalidation costs two packets.

=> / Home
""",
    "guestbook.py": '''\
"""Guestbook — a dynamic Meshsites handler.

A handler file defines handle(req) and returns a Meshdown page (str).
req has: method ("GET"/"POST"), path, query, form, args, sender, sender_num.
"""

from pathlib import Path

from meshsites import Store, sanitize

store = Store(Path(__file__).with_suffix(".log"))   # sanitized, 128 KB bounded


def handle(req):
    if req.method == "POST" and req.form.get("msg", "").strip():
        store.append({
            "who": req.form.get("name", "").strip() or req.sender,
            "msg": req.form["msg"].strip(),
        })

    lines = ["# Guestbook", ""]
    entries = store.entries()
    if entries:
        for e in reversed(entries[-10:]):
            lines.append("* %s: %s" % (sanitize(e.get("who", "?")),
                                       sanitize(e.get("msg", ""))))
    else:
        lines.append("No entries yet — be the first.")
    lines += [
        "",
        "[form post /guestbook]",
        "[field name Name]",
        "[field msg Message]",
        "[submit Sign]",
        "[/form]",
        "",
        "=> / Home",
    ]
    return "\\n".join(lines) + "\\n"
''',
}


def _resolve_name(root: Path, override: str | None) -> str:
    name = override
    if name is None:
        conf = root / "meshsite.toml"
        if conf.is_file():
            try:
                name = tomllib.loads(conf.read_text("utf-8")).get("name")
            except Exception as e:
                log.warning("could not parse %s: %s", conf, e)
    if not name:
        name = root.name or "meshsite"
    name = clean_text(name)
    while len(name.encode("utf-8")) > MAX_NAME_BYTES:
        name = name[:-1]
    if not name:
        name = "meshsite"
    return name


def cmd_serve(args) -> int:
    root = Path(args.site).resolve()
    if not root.is_dir():
        print(f"error: site directory {root} does not exist "
              f"(create one with: meshsites init {args.site})", file=sys.stderr)
        return 1
    if not any((root / f"index{ext}").is_file() for ext in (".md", ".py")):
        log.warning("%s has no index.md or index.py — GET / will answer "
                    "NOT_FOUND", root)
    name = _resolve_name(root, args.name)

    if args.tcp:
        from meshtastic.tcp_interface import TCPInterface
        connect = lambda: TCPInterface(args.tcp)
        where = f"tcp:{args.tcp}"
    else:
        from meshtastic.serial_interface import SerialInterface
        connect = lambda: SerialInterface(devPath=args.device)
        where = args.device or "serial:auto"

    log.info("meshsites %s — site %r from %s via %s", __version__, name, root, where)
    server = MeshsiteServer(Site(root, name), connect)
    try:
        import signal
        signal.signal(signal.SIGUSR1, lambda *_: server.beacon_now())
        log.info("SIGUSR1 triggers an immediate beacon (kill -USR1 %d)", os.getpid())
    except (ImportError, AttributeError, ValueError):
        pass  # no SIGUSR1 on this platform / not main thread
    try:
        server.run()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.stop()
    return 0


def cmd_init(args) -> int:
    root = Path(args.site)
    root.mkdir(parents=True, exist_ok=True)
    name = args.name or root.resolve().name or "My Meshsite"
    wrote = []
    for fname, template in SCAFFOLD.items():
        path = root / fname
        if path.exists():
            print(f"  skip {path} (exists)")
            continue
        path.write_text(template.replace("{name}", name), "utf-8")
        wrote.append(fname)
    print(f"Site ready in {root.resolve()}" +
          (f" ({', '.join(wrote)})" if wrote else ""))
    print(f"Serve it with: meshsites serve {args.site}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="meshsites",
        description="Serve tiny Meshdown sites over Meshtastic (protocol v1).")
    parser.add_argument("--version", action="version",
                        version=f"meshsites {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="serve a site directory over the radio")
    p.add_argument("site", nargs="?", default=".", help="site directory (default: .)")
    p.add_argument("--name", help="site name for beacons (default: from "
                                  "meshsite.toml, else directory name)")
    p.add_argument("--device", help="serial device (default: autodetect)")
    p.add_argument("--tcp", metavar="HOST", help="connect to a radio over TCP "
                                                 "instead of serial")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--log-file", metavar="PATH",
                   help="also append logs (access and errors) to this file")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("init", help="scaffold a new site directory")
    p.add_argument("site", help="directory to create")
    p.add_argument("--name", help="site name")
    p.set_defaults(func=cmd_init)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")
    if getattr(args, "log_file", None):
        handler = logging.FileHandler(args.log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
