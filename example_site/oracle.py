"""The Oracle Rabbit — ask a question, receive wisdom. GET form (cacheable!)."""

import hashlib
import time

from meshsites import sanitize

ANSWERS = [
    "Yes. Unquestionably yes.",
    "No. The warren has spoken.",
    "Ask again when the moon is over the antenna.",
    "Signs point to yes, but the SNR is low.",
    "Only if you bring carrots.",
    "The burrow is dark on this matter.",
    "Absolutely — but tell no fox.",
    "Outlook hazy. Retry after one hop.",
    "It is known. It is so.",
    "The rabbit twitches its nose: no.",
    "Dig deeper and ask again.",
    "All ears say yes.",
    "That path leads above ground. Careful.",
    "Yes, before the next beacon.",
    "Count your hops. The answer is one.",
    "The etag never lies: nothing has changed.",
]


def handle(req):
    q = sanitize(req.query.get("q", "").strip())[:80]
    lines = ["# The Oracle Rabbit", ""]
    if q:
        # Same question, same day -> same answer. Oracles are consistent.
        day = time.strftime("%Y%m%d")
        pick = int(hashlib.sha256((q + day).encode()).hexdigest(), 16) % len(ANSWERS)
        lines += [
            f"You asked: \"{q}\"",
            "",
            "The rabbit closes its eyes... whiskers tremble...",
            "",
            f"## {ANSWERS[pick]}",
            "",
        ]
    else:
        lines += ["Ask, and the warren answers. One question at a time.", ""]
    lines += [
        "[form get /oracle]",
        "[field q Your question]",
        "[submit Consult]",
        "[/form]",
        "",
        "=> / Main menu",
    ]
    return "\n".join(lines) + "\n"
