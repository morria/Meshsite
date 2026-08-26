"""The Burrow — message board. POST to add, GET to read."""

from pathlib import Path

from meshsites import Store, sanitize

store = Store(Path(__file__).with_suffix(".log"))


def handle(req):
    if req.method == "POST" and req.form.get("msg", "").strip():
        store.append({
            "who": req.form.get("name", "").strip()[:24] or req.sender,
            "msg": req.form["msg"].strip()[:160],
        })

    posts = store.entries()
    lines = ["# The Burrow", "",
             f"{len(posts)} post{'s' if len(posts) != 1 else ''} in the warren."]
    if posts:
        lines.append("")
        start = len(posts)
        for e in reversed(posts[-8:]):
            lines.append("* #%d %s: %s" % (start, sanitize(e.get("who", "?")),
                                           sanitize(e.get("msg", ""))))
            start -= 1
    lines += [
        "",
        "[form post /board]",
        "[field name Handle]",
        "[field msg Message]",
        "[submit Post]",
        "[/form]",
        "",
        "=> / Main menu",
    ]
    return "\n".join(lines) + "\n"
