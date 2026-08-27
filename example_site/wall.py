"""Graffiti Wall — one line, no names, no rules (well, three rules)."""

from pathlib import Path

from meshsites import Store, sanitize

store = Store(Path(__file__).with_suffix(".log"), max_bytes=8192)


def handle(req):
    if req.method == "POST" and req.form.get("line", "").strip():
        store.append({"line": req.form["line"].strip()[:80]})

    lines = ["# Graffiti Wall", "",
             "Spray one line. Anonymous. Old lines fade away.", ""]
    tags = store.entries()
    if tags:
        for e in reversed(tags[-12:]):
            lines.append("* %s" % sanitize(e.get("line", "")))
    else:
        lines.append("* (bare concrete. be the first)")
    lines += [
        "",
        "[form post /wall]",
        "[field line Your line]",
        "[submit Spray]",
        "[/form]",
        "",
        "=> / Main menu",
    ]
    return "\n".join(lines) + "\n"
