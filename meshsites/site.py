"""Page resolution: static Meshdown files and dynamic Python handlers.

Mapping (spec section 5, extended with dynamic handlers):
    /            -> index.py, else index.md
    /foo         -> foo.py, else foo.md
    /a/b         -> a/b.py, else a/b.md

A dynamic handler is a Python file defining ``handle(request)`` which returns
a Meshdown page as a str, or raises ``PageError`` for a protocol error.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, unquote

from . import protocol as P

log = logging.getLogger("meshsites.site")


class PageError(Exception):
    """Answer the request with an ERROR frame (code, optional short message)."""

    def __init__(self, code: int, message: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message


class NotFound(PageError):
    def __init__(self, message: str = ""):
        super().__init__(P.ERR_NOT_FOUND, message)


class BadRequest(PageError):
    def __init__(self, message: str = ""):
        super().__init__(P.ERR_BAD_REQUEST, message)


@dataclass
class Request:
    """What a dynamic handler receives."""

    method: str                     # "GET" or "POST"
    path: str                       # decoded path, query removed
    query: dict = field(default_factory=dict)   # parsed ?k=v pairs
    form: dict = field(default_factory=dict)    # parsed POST body pairs
    sender: str = "?"               # node id, e.g. "!a1b2c3d4"
    sender_num: int = 0             # numeric node id

    @property
    def args(self) -> dict:
        """Query and form merged; form wins on conflicts."""
        return {**self.query, **self.form}


_HANDLER_NAME = re.compile(r"[^0-9A-Za-z_]")


class Site:
    def __init__(self, root: str | Path, name: str):
        self.root = Path(root).resolve()
        self.name = name
        self._modules: dict[str, tuple[tuple, object]] = {}
        self._lock = threading.Lock()

    def render(self, frame: P.RequestFrame, sender_num: int, sender_id: str) -> str:
        """Resolve a parsed REQUEST to a Meshdown page. Raises PageError."""
        raw_path, _, raw_query = frame.target.partition("?")
        path = unquote(raw_path)
        # Traversal is checked after percent-decoding so %2e%2e is caught (spec 5)
        if not path.startswith("/") or ".." in path or "\x00" in path:
            raise BadRequest("bad path")
        rel = path.lstrip("/") or "index"
        parts = [p for p in rel.split("/") if p]
        if not parts or any(p.startswith(".") for p in parts):
            raise NotFound()
        base = self.root.joinpath(*parts)

        req = Request(
            method="POST" if frame.method == P.POST else "GET",
            path=path,
            query=dict(parse_qsl(raw_query, keep_blank_values=True)),
            form=dict(parse_qsl(frame.body.decode("utf-8", "replace"),
                                keep_blank_values=True)) if frame.method == P.POST else {},
            sender=sender_id,
            sender_num=sender_num,
        )

        for candidate, kind in ((base.with_name(base.name + ".py"), "py"),
                                (base.with_name(base.name + ".md"), "md")):
            if not candidate.is_file():
                continue
            # Belt and braces on top of the '..' check: never follow a
            # resolved path out of the site directory (e.g. via symlink).
            if not candidate.resolve().is_relative_to(self.root):
                raise NotFound()
            if kind == "py":
                return self._run_handler(candidate, req)
            return self._read_static(candidate)
        raise NotFound()

    def _read_static(self, path: Path) -> str:
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            # Exists but unreadable (e.g. cloud-sync placeholder) -> ERROR 4
            raise PageError(P.ERR_SERVER_ERROR, "page temporarily unreadable") from None
        if not text:
            raise NotFound()  # an empty file is not a page (spec 5)
        return text

    def _run_handler(self, path: Path, req: Request) -> str:
        try:
            mod = self._load_module(path)
        except PageError:
            raise
        except Exception:
            log.exception("failed to load handler %s", path)
            raise PageError(P.ERR_SERVER_ERROR, "handler failed to load") from None
        fn = getattr(mod, "handle", None)
        if not callable(fn):
            log.error("handler %s has no handle() function", path)
            raise PageError(P.ERR_SERVER_ERROR, "handler misconfigured")
        try:
            page = fn(req)
        except PageError:
            raise
        except Exception:
            log.exception("handler %s raised", path)
            raise PageError(P.ERR_SERVER_ERROR, "handler error") from None
        if not isinstance(page, str):
            log.error("handler %s returned %r, expected str", path, type(page))
            raise PageError(P.ERR_SERVER_ERROR, "handler returned non-text")
        if not page:
            raise NotFound()
        return page

    def _load_module(self, path: Path):
        """Import (and re-import on change) a handler file."""
        key = str(path)
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        with self._lock:
            cached = self._modules.get(key)
            if cached and cached[0] == stamp:
                return cached[1]
            modname = "meshsites_handler_" + _HANDLER_NAME.sub("_", key)
            spec = importlib.util.spec_from_file_location(modname, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._modules[key] = (stamp, mod)
            return mod
