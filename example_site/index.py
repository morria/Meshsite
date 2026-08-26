"""GRANGES BASE BBS — main menu. Dynamic: call counter, last caller, activity."""

from pathlib import Path

from meshsites import Store, sanitize

HERE = Path(__file__).parent
_board = Store(HERE / "board.log")


def _bump_calls(sender):
    """Classic BBS call counter + last-caller, tiny files next to the site."""
    cf, lf = HERE / "calls.count", HERE / "last.caller"
    try:
        n = int(cf.read_text()) + 1
    except Exception:
        n = 1
    try:
        last = lf.read_text().strip()
    except Exception:
        last = ""
    cf.write_text(str(n))
    lf.write_text(sanitize(sender)[:20])
    return n, last


def handle(req):
    n, last = _bump_calls(req.sender)
    lines = [
        "# 🐇 THE WARREN BBS",
        "",
        "Welcome to The Warren — a burrow on the mesh.",
        "No internet was harmed in serving this page.",
        "",
        f"* You are call #{n}",
    ]
    if last and last != req.sender:
        lines.append(f"* Last caller before you: {last}")
    posts = _board.entries()
    if posts:
        e = posts[-1]
        lines.append("* Fresh on the board: \"%s\" — %s" %
                     (sanitize(e.get("msg", ""))[:48],
                      sanitize(e.get("who", "?"))[:16]))
    lines += [
        "",
        "## Boards",
        "=> /board The Burrow — message board",
        "=> /wall Graffiti Wall — leave your mark",
        "=> /guestbook Logbook — sign in, traveler",
        "",
        "## Amusements",
        "=> /oracle Ask the Oracle Rabbit",
        "",
        "---",
        "=> /about About this system",
    ]
    return "\n".join(lines) + "\n"
