"""Meshsites protocol v1 (draft 7) — frame encoding/decoding, etag, compression.

Wire format reference: see the spec, sections 1-3.5. All multi-byte integers
are big-endian. The first payload byte is the frame type.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

# Transport constants (spec section 1)
PORTNUM = 421
MAX_FRAME = 200          # max Meshsites frame payload, bytes
CHUNK_DATA = 190         # max data bytes per CHUNK
MAX_CHUNKS = 16
MAX_COMPRESSED = CHUNK_DATA * MAX_CHUNKS  # 3040 bytes, hard response cap
MAX_PAGE_BYTES = 65536   # decompressed cap (client-side bomb defense)
MAX_PATH_BYTES = 120
MAX_ERROR_MSG_BYTES = 120
MAX_NAME_BYTES = 40

VERSION = 1              # highest protocol version we speak
MIN_VERSION = 1          # lowest protocol version we accept

# Frame types (spec section 2)
BEACON = 0x01
REQUEST = 0x02
CHUNK = 0x03
ERROR = 0x04
NOT_MODIFIED = 0x05

# Methods
GET = 0
POST = 1

# Error codes (spec section 2, frame 0x04)
ERR_NOT_FOUND = 1
ERR_TOO_LARGE = 2
ERR_BAD_REQUEST = 3
ERR_SERVER_ERROR = 4
ERR_BUSY = 5
ERR_UNSUPPORTED_VERSION = 6

ERROR_NAMES = {
    ERR_NOT_FOUND: "NOT_FOUND",
    ERR_TOO_LARGE: "TOO_LARGE",
    ERR_BAD_REQUEST: "BAD_REQUEST",
    ERR_SERVER_ERROR: "SERVER_ERROR",
    ERR_BUSY: "BUSY",
    ERR_UNSUPPORTED_VERSION: "UNSUPPORTED_VERSION",
}


def error_name(code: int) -> str:
    return ERROR_NAMES.get(code, "code %d" % code)

# Characters that must never reach displays or storage: C0/C1 controls, DEL,
# and bidirectional-override/isolate characters (spec sections 2, 5).
_BIDI = "‪‫‬‭‮⁦⁧⁨⁩"
_STRIP = {c: None for c in range(0x20)}
_STRIP.update({c: None for c in range(0x7F, 0xA0)})
_STRIP.update({ord(c): None for c in _BIDI})


def clean_text(text: str) -> str:
    """Strip control and bidi-override characters (untrusted display text)."""
    return text.translate(_STRIP)


def fnv1a32(data: bytes) -> int:
    """FNV-1a 32-bit over the uncompressed page bytes; 0 becomes 1 (spec 3.5)."""
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h or 1


def deflate(data: bytes) -> bytes:
    """Raw DEFLATE (RFC 1951, no zlib/gzip header)."""
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def inflate(data: bytes, limit: int = MAX_PAGE_BYTES) -> bytes:
    """Raw-DEFLATE decompress with a hard output cap (bomb defense)."""
    d = zlib.decompressobj(-15)
    out = d.decompress(data, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise ValueError("decompressed page exceeds %d bytes" % limit)
    return out


class FrameError(Exception):
    """A REQUEST we could not accept. If req_id is None the frame is dropped
    silently; otherwise the server answers ERROR `code` echoing req_id."""

    def __init__(self, code: int, message: str = "", req_id: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.req_id = req_id


@dataclass(frozen=True)
class RequestFrame:
    version: int
    req_id: int
    method: int          # GET / POST
    etag: int            # already normalized to 0 for POST
    target: str          # path+query exactly as received (decoded UTF-8)
    body: bytes
    cache_key: bytes     # full frame bytes, POST etag normalized to 0 (spec 3)


def parse_request(payload: bytes) -> RequestFrame:
    """Parse a 0x02 REQUEST payload (first byte already checked).

    Raises FrameError per spec: version 0 -> ERROR 6 (never 3); other
    malformations with a parseable id -> ERROR 3; id unparseable -> silent.
    """
    if len(payload) < 4:
        # id not parseable -> drop silently
        raise FrameError(ERR_BAD_REQUEST, "", None)
    version = payload[1]
    req_id = struct.unpack(">H", payload[2:4])[0]
    if version == 0:
        raise FrameError(ERR_UNSUPPORTED_VERSION,
                         "supported versions %d-%d" % (MIN_VERSION, VERSION), req_id)

    def bad(msg: str) -> FrameError:
        return FrameError(ERR_BAD_REQUEST, msg, req_id)

    if req_id == 0:
        raise bad("id must be nonzero")
    if len(payload) < 10:
        raise bad("short frame")
    method = payload[4]
    if method not in (GET, POST):
        raise bad("bad method")
    (etag,) = struct.unpack(">I", payload[5:9])
    path_len = payload[9]
    if path_len == 0:
        raise bad("empty path")
    if path_len > MAX_PATH_BYTES:
        raise bad("path too long")
    if len(payload) < 10 + path_len:
        raise bad("truncated path")
    try:
        target = payload[10:10 + path_len].decode("utf-8")
    except UnicodeDecodeError:
        raise bad("path not UTF-8") from None
    if not target.startswith("/"):
        raise bad("path must start with /")
    body = payload[10 + path_len:]
    if method == GET and body:
        raise bad("body bytes on GET")
    if method == POST:
        # etag is ignored on POST; normalize to 0 in both the parsed frame and
        # the cache key so an etag-varying POST retry stays cacheable (spec 3).
        etag = 0
        cache_key = payload[:5] + b"\x00\x00\x00\x00" + payload[9:]
    else:
        cache_key = payload
    return RequestFrame(version, req_id, method, etag, target, body, cache_key)


def encode_beacon(name: str) -> bytes:
    raw = clean_text(name).encode("utf-8")
    if not (1 <= len(raw) <= MAX_NAME_BYTES):
        raise ValueError("beacon name must be 1-%d UTF-8 bytes" % MAX_NAME_BYTES)
    return bytes([BEACON, VERSION]) + raw


def encode_chunk(version: int, req_id: int, seq: int, total: int,
                 etag: int, data: bytes) -> bytes:
    return struct.pack(">BBHBBI", CHUNK, version, req_id, seq, total, etag) + data


def encode_error(req_id: int, code: int, message: str = "") -> bytes:
    raw = message.encode("utf-8")[:MAX_ERROR_MSG_BYTES]
    while raw:  # don't cut a UTF-8 sequence mid-codepoint
        try:
            raw.decode("utf-8")
            break
        except UnicodeDecodeError:
            raw = raw[:-1]
    return struct.pack(">BHB", ERROR, req_id, code) + raw


def encode_not_modified(req_id: int, etag: int) -> bytes:
    return struct.pack(">BHI", NOT_MODIFIED, req_id, etag)


def encode_request(req_id: int, method: int, target: str,
                   etag: int = 0, body: bytes = b"", version: int = VERSION) -> bytes:
    """Build a REQUEST frame (used by tests and future client code)."""
    raw = target.encode("utf-8")
    if len(raw) > MAX_PATH_BYTES:
        raise ValueError("path+query exceeds %d bytes" % MAX_PATH_BYTES)
    frame = struct.pack(">BBHBIB", REQUEST, version, req_id, method,
                        etag, len(raw)) + raw + body
    if len(frame) > MAX_FRAME:
        raise ValueError("request frame exceeds %d bytes" % MAX_FRAME)
    return frame


def make_chunks(version: int, req_id: int, page: bytes, etag: int) -> list[bytes]:
    """Compress a page and split it into CHUNK frames.

    Raises FrameError(ERR_TOO_LARGE) if the page doesn't fit 16 chunks.
    """
    comp = deflate(page)
    if len(comp) > MAX_COMPRESSED:
        raise FrameError(ERR_TOO_LARGE, "page too large", req_id)
    total = max(1, -(-len(comp) // CHUNK_DATA))
    return [
        encode_chunk(version, req_id, seq, total, etag,
                     comp[seq * CHUNK_DATA:(seq + 1) * CHUNK_DATA])
        for seq in range(total)
    ]
