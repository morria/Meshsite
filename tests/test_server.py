import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from meshsites import protocol as P
from meshsites.server import MeshsiteServer
from meshsites.site import Site

SERVER_NUM = 0x11111111
CLIENT_NUM = 0x22222222


class FakeIface:
    """Records sendData calls; acks (or naks) immediately via onResponse."""

    def __init__(self):
        self.sent = []
        self.nak_after = None  # ack the first N sends, then NAK
        self.myInfo = SimpleNamespace(my_node_num=SERVER_NUM)
        self._lock = threading.Lock()

    def sendData(self, data, destinationId="^all", portNum=256, wantAck=False,
                 channelIndex=0, hopLimit=None, onResponse=None,
                 onResponseAckPermitted=False, **kw):
        with self._lock:
            n = len(self.sent)
            self.sent.append(dict(data=bytes(data), dest=destinationId,
                                  port=portNum, want_ack=wantAck,
                                  channel=channelIndex, hop_limit=hopLimit))
        if onResponse is not None:
            reason = "NONE"
            if self.nak_after is not None and n >= self.nak_after:
                reason = "MAX_RETRANSMIT"
            onResponse({"decoded": {"routing": {"errorReason": reason}}})

    def close(self):
        pass


def raw_packet(payload, frm=CLIENT_NUM, to=SERVER_NUM, hop_start=1, hop_limit=1):
    raw = SimpleNamespace(
        decoded=SimpleNamespace(portnum=P.PORTNUM, payload=payload),
        hop_start=hop_start, hop_limit=hop_limit, pki_encrypted=False,
        HasField=lambda f: f == "decoded")
    return {"raw": raw, "from": frm, "to": to, "fromId": "!%08x" % frm}


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "index.md").write_text("# Home\n\nHello mesh.\n")
        import random as _r
        rng = _r.Random(42)   # ~1.5 KB compressed -> several chunks, under the cap
        (root / "big.md").write_text("# Big\n" + "".join(
            rng.choice("abcdefghijklmnopqrstuvwxyz \n") for _ in range(2500)))
        self.site = Site(root, "TestSite")
        self.server = MeshsiteServer(self.site, connect=None)
        self.iface = FakeIface()
        self.server.iface = self.iface
        self.server.my_num = SERVER_NUM

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, target="/", req_id=0x1234, method=P.GET, etag=0,
                body=b"", **pkt):
        frame = P.encode_request(req_id, method, target, etag=etag, body=body)
        self.server._on_receive(raw_packet(frame, **pkt))

    def wait_sent(self, n, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.iface.sent) >= n and not self.server._inflight:
                return self.iface.sent
            time.sleep(0.01)
        self.fail("expected >=%d sends, got %d" % (n, len(self.iface.sent)))

    # ------------------------------------------------------------- happy path

    def test_get_index_chunked_response(self):
        self.request("/")
        sent = self.wait_sent(1)
        self.assertEqual(len(sent), 1)
        s = sent[0]
        self.assertEqual((s["port"], s["hop_limit"], s["channel"], s["dest"],
                          s["want_ack"]),
                         (P.PORTNUM, 1, 0, CLIENT_NUM, True))
        t, v, rid, seq, total, etag = struct.unpack(">BBHBBI", s["data"][:10])
        page = (Path(self.tmp.name) / "index.md").read_text().encode()
        self.assertEqual((t, v, rid, seq, total, etag),
                         (P.CHUNK, 1, 0x1234, 0, 1, P.fnv1a32(page)))
        self.assertEqual(P.inflate(s["data"][10:]), page)

    def test_multi_chunk_page_paced_and_reassembles(self):
        self.request("/big", req_id=0x2222)
        sent = self.wait_sent(2)
        totals = {struct.unpack(">BBHBBI", s["data"][:10])[4] for s in sent}
        self.assertEqual(len(sent), totals.pop())
        blob = b"".join(s["data"][10:] for s in sent)
        self.assertEqual(P.inflate(blob),
                         (Path(self.tmp.name) / "big.md").read_bytes())

    def test_not_modified_on_matching_etag(self):
        page = (Path(self.tmp.name) / "index.md").read_bytes()
        self.request("/", etag=P.fnv1a32(page))
        sent = self.wait_sent(1)
        self.assertEqual(sent[0]["data"],
                         P.encode_not_modified(0x1234, P.fnv1a32(page)))
        self.assertTrue(sent[0]["want_ack"])

    def test_post_never_not_modified(self):
        (Path(self.tmp.name) / "h.py").write_text(
            "def handle(req):\n    return '# ok\\n'\n")
        etag = P.fnv1a32(b"# ok\n")
        self.request("/h", method=P.POST, body=b"a=1")
        sent = self.wait_sent(1)
        self.assertEqual(sent[0]["data"][0], P.CHUNK)

    # ----------------------------------------------------------------- errors

    def test_not_found(self):
        self.request("/nope", req_id=0x0BAD)
        sent = self.wait_sent(1)
        self.assertEqual(sent[0]["data"][:4], struct.pack(">BHB", P.ERROR,
                                                          0x0BAD, P.ERR_NOT_FOUND))
        self.assertFalse(sent[0]["want_ack"])  # ERROR must not want ack

    def test_version_zero_gets_error_6(self):
        frame = bytearray(P.encode_request(0x0007, P.GET, "/"))
        frame[1] = 0
        self.server._on_receive(raw_packet(bytes(frame)))
        sent = self.wait_sent(1)
        self.assertEqual(sent[0]["data"][:4],
                         struct.pack(">BHB", P.ERROR, 7, P.ERR_UNSUPPORTED_VERSION))

    def test_get_with_body_gets_error_3(self):
        frame = P.encode_request(0x0008, P.GET, "/") + b"junk"
        self.server._on_receive(raw_packet(frame))
        sent = self.wait_sent(1)
        self.assertEqual(sent[0]["data"][:4],
                         struct.pack(">BHB", P.ERROR, 8, P.ERR_BAD_REQUEST))

    def test_too_large_page(self):
        import random as r
        rng = r.Random(1)
        (Path(self.tmp.name) / "huge.md").write_text(
            "".join(chr(rng.randrange(0x4E00, 0x9FFF)) for _ in range(4000)))
        self.request("/huge", req_id=0x0009)
        sent = self.wait_sent(1)
        self.assertEqual(sent[0]["data"][:4],
                         struct.pack(">BHB", P.ERROR, 9, P.ERR_TOO_LARGE))

    # ----------------------------------------------------- transport hygiene

    def test_relayed_frame_dropped(self):
        self.request("/", hop_start=3, hop_limit=2)
        time.sleep(0.3)
        self.assertEqual(self.iface.sent, [])

    def test_frame_for_other_node_ignored(self):
        self.request("/", to=0x33333333)
        time.sleep(0.3)
        self.assertEqual(self.iface.sent, [])

    def test_oversize_frame_ignored(self):
        self.server._on_receive(raw_packet(b"\x02" + b"\x01" * 250))
        time.sleep(0.3)
        self.assertEqual(self.iface.sent, [])

    def test_unknown_frame_type_ignored(self):
        self.server._on_receive(raw_packet(b"\x7f\x01\x02\x03"))
        time.sleep(0.3)
        self.assertEqual(self.iface.sent, [])

    # --------------------------------------------------- busy / dup / cache

    def test_duplicate_inflight_dropped_other_id_busy(self):
        frame = P.encode_request(0x00AA, P.GET, "/")
        key = P.parse_request(frame).cache_key
        with self.server._state:
            self.server._inflight[CLIENT_NUM] = (0x00AA, key)
        # exact duplicate -> silent drop
        self.server._on_receive(raw_packet(frame))
        time.sleep(0.2)
        self.assertEqual(self.iface.sent, [])
        # different request while busy -> ERROR 5
        self.request("/other", req_id=0x00BB)
        time.sleep(0.2)
        with self.server._state:
            self.server._inflight.pop(CLIENT_NUM, None)
        self.assertEqual(self.iface.sent[0]["data"][:4],
                         struct.pack(">BHB", P.ERROR, 0xBB, P.ERR_BUSY))

    def test_retry_served_from_cache_without_rerender(self):
        calls = []
        orig = self.site.render
        self.site.render = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
        self.request("/", req_id=0x00CC)
        self.wait_sent(1)
        self.request("/", req_id=0x00CC)   # retry, same id + content
        self.wait_sent(2)
        self.assertEqual(len(calls), 1)    # rendered once
        self.assertEqual(self.iface.sent[0]["data"], self.iface.sent[1]["data"])

    def test_same_id_different_content_rerenders(self):
        (Path(self.tmp.name) / "two.md").write_text("# Two\n")
        self.request("/", req_id=0x00DD)
        self.wait_sent(1)
        self.request("/two", req_id=0x00DD)  # id collision, new content
        sent = self.wait_sent(2)
        self.assertEqual(P.inflate(sent[1]["data"][10:]), b"# Two\n")

    def test_nak_aborts_remaining_chunks(self):
        self.iface.nak_after = 1   # ack chunk 0, NAK chunk 1
        self.request("/big", req_id=0x00EE)
        time.sleep(0.5)
        page = (Path(self.tmp.name) / "big.md").read_bytes()
        expected_total = len(P.make_chunks(1, 1, page, 1))
        self.assertGreater(expected_total, 2)
        self.assertEqual(len(self.iface.sent), 2)  # aborted after the NAK


class TestBeacon(unittest.TestCase):
    def test_beacon_payload_and_addressing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "index.md").write_text("# x\n")
            server = MeshsiteServer(Site(Path(tmp), "My Site"), connect=None,
                                    beacon_interval=9999)
            iface = FakeIface()
            server.iface = iface
            server.my_num = SERVER_NUM
            t = threading.Thread(target=server._beacon_loop, daemon=True)
            t.start()
            time.sleep(0.3)
            server.stop()
            self.assertGreaterEqual(len(iface.sent), 1)
            b = iface.sent[0]
            self.assertEqual(b["data"], b"\x01\x01My Site")
            self.assertEqual((b["dest"], b["hop_limit"], b["want_ack"]),
                             ("^all", 1, False))


if __name__ == "__main__":
    unittest.main()
