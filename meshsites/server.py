"""The Meshsites server: radio I/O, beacons, request handling, response cache.

Transport rules implemented here (spec sections 1, 3):
  - port 421, primary channel, hop_limit=1 on every frame
  - relay-discard on receive (hop_start > 0 and hop_limit < hop_start)
  - one in-flight response per requester; duplicates dropped, others BUSY
  - (requester, id) response cache, 2 min TTL, size-bounded
  - chunk pacing: next chunk after ack or 8 s; abort remaining chunks on NAK
  - beacon on each (re)connect and every 5 min +/- 30 s
"""

from __future__ import annotations

import collections
import logging
import random
import threading
import time

from pubsub import pub

from . import protocol as P
from .site import PageError, Site

log = logging.getLogger("meshsites.server")
access = logging.getLogger("meshsites.access")

CACHE_TTL = 120.0
CACHE_MAX = 128
ACK_TIMEOUT = 8.0
BEACON_INTERVAL = 300.0
BEACON_JITTER = 30.0
MAX_CONCURRENT = 8
BROADCAST = 0xFFFFFFFF


class MeshsiteServer:
    def __init__(self, site: Site, connect, *,
                 beacon_interval: float = BEACON_INTERVAL,
                 beacon_jitter: float = BEACON_JITTER):
        self.site = site
        self._connect = connect          # () -> meshtastic interface
        self.iface = None
        self.my_num: int | None = None
        self.beacon_interval = beacon_interval
        self.beacon_jitter = beacon_jitter
        self._beacon = P.encode_beacon(site.name)

        self._send_lock = threading.Lock()   # serialize radio writes (spec 3)
        self._state = threading.Lock()
        self._inflight: dict[int, tuple[int, bytes]] = {}  # sender -> (id, key)
        # (sender, id) -> (cache_key, kind, payloads, stored_at)
        self._cache: collections.OrderedDict = collections.OrderedDict()
        self._stop = threading.Event()
        self._lost = threading.Event()

    # ------------------------------------------------------------- lifecycle

    def run(self) -> None:
        """Connect, serve, reconnect on radio loss. Blocks until stop()."""
        pub.subscribe(self._on_receive, "meshtastic.receive")
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
        backoff = 5.0
        try:
            while not self._stop.is_set():
                try:
                    self.iface = self._connect()
                    self.my_num = self.iface.myInfo.my_node_num
                except Exception as e:
                    log.warning("radio connect failed: %s — retrying in %.0fs", e, backoff)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 60.0)
                    continue
                backoff = 5.0
                self._lost.clear()
                log.info("serving %r as !%08x on port %d",
                         self.site.name, self.my_num, P.PORTNUM)
                beacon_thread = threading.Thread(
                    target=self._beacon_loop, name="meshsites-beacon", daemon=True)
                beacon_thread.start()
                while not self._stop.is_set() and not self._lost.is_set():
                    self._lost.wait(1.0)
                self._close_iface()
                if self._lost.is_set() and not self._stop.is_set():
                    log.warning("radio connection lost — reconnecting")
        finally:
            self._close_iface()
            pub.unsubscribe(self._on_receive, "meshtastic.receive")
            pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")

    def stop(self) -> None:
        self._stop.set()

    def _close_iface(self) -> None:
        iface, self.iface = self.iface, None
        if iface is not None:
            try:
                iface.close()
            except Exception:
                pass

    def _on_connection_lost(self, interface=None) -> None:
        self._lost.set()

    # --------------------------------------------------------------- beacons

    def _beacon_loop(self) -> None:
        """Beacon immediately on (re)connect, then every 5 min +/- 30 s."""
        while not self._stop.is_set() and not self._lost.is_set():
            try:
                self._send(BROADCAST, self._beacon, want_ack=False)
                log.debug("beacon sent (%r)", self.site.name)
            except Exception as e:
                log.warning("beacon send failed: %s", e)
            delay = self.beacon_interval + random.uniform(-self.beacon_jitter,
                                                          self.beacon_jitter)
            deadline = time.monotonic() + max(delay, 1.0)
            while time.monotonic() < deadline:
                if self._stop.is_set() or self._lost.is_set():
                    return
                time.sleep(0.5)

    # --------------------------------------------------------------- receive

    def _on_receive(self, packet, interface=None) -> None:
        try:
            self._handle_packet(packet)
        except Exception:
            log.exception("error handling packet")

    def _handle_packet(self, packet: dict) -> None:
        raw = packet.get("raw")
        if raw is None or not raw.HasField("decoded"):
            return
        if raw.decoded.portnum != P.PORTNUM:
            return
        sender = packet.get("from")
        if sender in (None, 0, BROADCAST) or sender == self.my_num:
            return
        if packet.get("to") != self.my_num:
            return  # unicast for someone else, overheard on the shared channel
        payload = bytes(raw.decoded.payload)
        if not payload or len(payload) > P.MAX_FRAME:
            return  # malformed size — ignore (spec 1)
        # Relay-discard: only direct RF neighbors may talk Meshsites (spec 1)
        if raw.hop_start > 0 and raw.hop_limit < raw.hop_start:
            log.debug("dropping relayed frame from !%08x", sender)
            return
        if getattr(raw, "pki_encrypted", False):
            return  # PKI unicast must not be used (spec 1)
        if payload[0] != P.REQUEST:
            return  # servers consume only REQUESTs; unknown types ignored

        sender_id = packet.get("fromId") or "!%08x" % sender
        try:
            frame = P.parse_request(payload)
        except P.FrameError as e:
            if e.req_id is not None:
                self._send_error(sender, e.req_id, e.code, e.message)
                access.info("%s malformed request -> ERROR %d %s%s", sender_id,
                            e.code, P.error_name(e.code),
                            " (%s)" % e.message if e.message else "")
            else:
                access.info("%s malformed request (id unparseable) -> dropped",
                            sender_id)
            return
        if frame.version < P.MIN_VERSION:
            self._send_error(sender, frame.req_id, P.ERR_UNSUPPORTED_VERSION,
                             "supported versions %d-%d" % (P.MIN_VERSION, P.VERSION))
            access.info("%s v%d request -> ERROR 6 UNSUPPORTED_VERSION",
                        sender_id, frame.version)
            return

        with self._state:
            current = self._inflight.get(sender)
            if current is not None:
                if current == (frame.req_id, frame.cache_key):
                    log.debug("duplicate of in-flight request %04x — dropped",
                              frame.req_id)
                    return  # link-level retransmit; BUSY would be wrong (spec 3)
                self._send_error(sender, frame.req_id, P.ERR_BUSY, "busy")
                access.info("%s %s -> ERROR 5 BUSY (request in flight)",
                            sender_id, frame.target)
                return
            if len(self._inflight) >= MAX_CONCURRENT:
                self._send_error(sender, frame.req_id, P.ERR_BUSY, "busy")
                access.info("%s %s -> ERROR 5 BUSY (server at capacity)",
                            sender_id, frame.target)
                return
            self._inflight[sender] = (frame.req_id, frame.cache_key)

        threading.Thread(target=self._serve, args=(sender, sender_id, frame),
                         name="meshsites-req-%04x" % frame.req_id,
                         daemon=True).start()

    # ----------------------------------------------------------------- serve

    def _serve(self, sender: int, sender_id: str, frame: P.RequestFrame) -> None:
        started = time.monotonic()
        method = "POST" if frame.method == P.POST else "GET"
        try:
            kind, payloads = self._cached_response(sender, frame)
            cached = kind is not None
            if not cached:
                kind, payloads = self._render(sender, sender_id, frame)
                if kind in ("chunks", "not_modified"):
                    self._cache_store(sender, frame, kind, payloads)
            outcome = self._transmit(sender, frame.req_id, kind, payloads)
            access.info("%s %s %s -> %s in %.1fs%s", sender_id, method,
                        frame.target, outcome, time.monotonic() - started,
                        " (from response cache)" if cached else "")
        except Exception:
            log.exception("failed serving %04x for %s", frame.req_id, sender_id)
            access.info("%s %s %s -> internal failure (see error log)",
                        sender_id, method, frame.target)
        finally:
            with self._state:
                self._inflight.pop(sender, None)

    def _cached_response(self, sender, frame):
        """Re-serve an identical retried request from the response cache."""
        now = time.monotonic()
        with self._state:
            entry = self._cache.get((sender, frame.req_id))
            if entry is not None:
                key, kind, payloads, stored = entry
                if key == frame.cache_key and now - stored <= CACHE_TTL:
                    log.debug("re-serving %04x from response cache", frame.req_id)
                    return kind, payloads
        return None, None

    def _cache_store(self, sender, frame, kind, payloads) -> None:
        now = time.monotonic()
        with self._state:
            self._cache[(sender, frame.req_id)] = (frame.cache_key, kind,
                                                   payloads, now)
            self._cache.move_to_end((sender, frame.req_id))
            for k in [k for k, v in self._cache.items() if now - v[3] > CACHE_TTL]:
                del self._cache[k]
            while len(self._cache) > CACHE_MAX:   # unauthenticated senders:
                self._cache.popitem(last=False)   # bound and evict oldest

    def _render(self, sender: int, sender_id: str, frame: P.RequestFrame):
        """Produce ('error'|'not_modified'|'chunks', [payloads])."""
        try:
            page = self.site.render(frame, sender, sender_id)
        except PageError as e:
            return "error", [P.encode_error(frame.req_id, e.code, e.message)]
        except Exception:
            log.exception("unexpected render failure")
            return "error", [P.encode_error(frame.req_id, P.ERR_SERVER_ERROR,
                                            "internal error")]
        data = page.encode("utf-8")
        etag = P.fnv1a32(data)
        if frame.method == P.GET and frame.etag == etag:
            return "not_modified", [P.encode_not_modified(frame.req_id, etag)]
        version = min(frame.version, P.VERSION)
        try:
            chunks = P.make_chunks(version, frame.req_id, data, etag)
        except P.FrameError as e:
            return "error", [P.encode_error(frame.req_id, e.code, e.message)]
        return "chunks", chunks

    def _transmit(self, sender: int, req_id: int, kind: str, payloads) -> str:
        if kind == "error":
            self._send(sender, payloads[0], want_ack=False)  # spec: no ack on ERROR
            code = payloads[0][3]
            return "ERROR %d %s" % (code, P.error_name(code))
        if kind == "not_modified":
            self._send_paced(sender, payloads[0])
            return "NOT_MODIFIED"
        total_bytes = sum(len(p) - 10 for p in payloads)
        for seq, payload in enumerate(payloads):
            result = self._send_paced(sender, payload)
            if result == "nak":
                # Peer unreachable — abort remaining chunks; the cached
                # response still serves a later retry (spec 3).
                log.info("NAK on chunk %d/%d of %04x — aborting response",
                         seq + 1, len(payloads), req_id)
                return "aborted at chunk %d/%d (NAK)" % (seq + 1, len(payloads))
            log.debug("chunk %d/%d of %04x: %s", seq + 1, len(payloads),
                      req_id, result)
        return "%d chunk%s, %d bytes deflated" % (
            len(payloads), "" if len(payloads) == 1 else "s", total_bytes)

    # ------------------------------------------------------------------ send

    def _send(self, dest: int, payload: bytes, *, want_ack: bool,
              on_response=None) -> None:
        iface = self.iface
        if iface is None:
            raise RuntimeError("radio not connected")
        with self._send_lock:
            iface.sendData(
                payload,
                destinationId=dest if dest != BROADCAST else "^all",
                portNum=P.PORTNUM,
                wantAck=want_ack,
                channelIndex=0,
                hopLimit=1,
                onResponse=on_response,
                onResponseAckPermitted=on_response is not None,
            )

    def _send_paced(self, dest: int, payload: bytes) -> str:
        """Send with want_ack; wait for ack/nak or 8 s. -> 'ack'|'nak'|'timeout'"""
        done = threading.Event()
        outcome = {}

        def onAckNak(response):  # name matters: meshtastic passes plain ACKs too
            routing = (response.get("decoded") or {}).get("routing") or {}
            reason = routing.get("errorReason", "NONE")
            outcome["result"] = "ack" if reason in ("NONE", 0, None) else "nak"
            done.set()

        try:
            self._send(dest, payload, want_ack=True, on_response=onAckNak)
        except Exception as e:
            log.warning("send to !%08x failed: %s", dest, e)
            return "nak"
        done.wait(ACK_TIMEOUT)
        return outcome.get("result", "timeout")

    def _send_error(self, dest: int, req_id: int, code: int, message: str = "") -> None:
        try:
            self._send(dest, P.encode_error(req_id, code, message), want_ack=False)
        except Exception as e:
            log.warning("error frame send failed: %s", e)
