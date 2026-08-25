"""Size-bounded, injection-sanitized persistence for dynamic handlers.

Spec section 5: stored form input MUST be sanitized against storage-format
injection (strip newlines and control characters) and MUST be size-bounded
(128 KB in this implementation family, oldest entries dropped).

One entry per line, URL-encoded key=value pairs. Oldest lines are dropped
when the file would exceed max_bytes.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from .protocol import clean_text

MAX_BYTES = 128 * 1024


def sanitize(text: str) -> str:
    """Strip newlines, control characters, and bidi overrides from user input.

    Use this on anything user-supplied before rendering it into a Meshdown
    page — it also prevents Meshdown line injection.
    """
    return clean_text(str(text).replace("\r", " ").replace("\n", " "))


class Store:
    """Append-only line store with oldest-first eviction."""

    def __init__(self, path: str | Path, max_bytes: int = MAX_BYTES):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def append(self, fields: dict) -> None:
        pairs = [(sanitize(k), sanitize(v)) for k, v in fields.items()]
        pairs.append(("ts", str(int(time.time()))))
        line = urlencode(pairs) + "\n"
        with self._lock:
            lines = self._read_lines()
            lines.append(line)
            total = sum(len(l.encode("utf-8")) for l in lines)
            while lines and total > self.max_bytes:
                total -= len(lines.pop(0).encode("utf-8"))
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("".join(lines), "utf-8")
            os.replace(tmp, self.path)

    def entries(self) -> list[dict]:
        """All stored entries, oldest first."""
        with self._lock:
            lines = self._read_lines()
        return [dict(parse_qsl(l.strip(), keep_blank_values=True)) for l in lines if l.strip()]

    def _read_lines(self) -> list[str]:
        try:
            return self.path.read_text("utf-8", errors="replace").splitlines(keepends=True)
        except FileNotFoundError:
            return []
