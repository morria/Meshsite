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
    return "\n".join(lines) + "\n"
