"""Meshsites — serve tiny Meshdown pages over Meshtastic (protocol v1).

Public API for dynamic handlers:

    from meshsites import PageError, NotFound, BadRequest, Store, sanitize

    def handle(req):            # req: meshsites.Request
        return "# Hello\\n"
"""

from .protocol import (
    ERR_BAD_REQUEST,
    ERR_BUSY,
    ERR_NOT_FOUND,
    ERR_SERVER_ERROR,
    ERR_TOO_LARGE,
    ERR_UNSUPPORTED_VERSION,
)
from .site import BadRequest, NotFound, PageError, Request, Site
from .store import Store, sanitize

__version__ = "0.1.0"

__all__ = [
    "BadRequest", "NotFound", "PageError", "Request", "Site", "Store",
    "sanitize", "__version__",
    "ERR_NOT_FOUND", "ERR_TOO_LARGE", "ERR_BAD_REQUEST", "ERR_SERVER_ERROR",
    "ERR_BUSY", "ERR_UNSUPPORTED_VERSION",
]
