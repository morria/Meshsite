import struct
import unittest

from meshsites import protocol as P


class TestEtag(unittest.TestCase):
    def test_known_vectors(self):
        # Standard FNV-1a 32 test vectors
        self.assertEqual(P.fnv1a32(b""), 2166136261)
        self.assertEqual(P.fnv1a32(b"a"), 0xE40C292C)
        self.assertEqual(P.fnv1a32(b"foobar"), 0xBF9CF968)

    def test_zero_becomes_one(self):
        h = P.fnv1a32(b"")
        self.assertNotEqual(h, 0)  # sanity: our vectors are nonzero anyway

    def test_spec_formula(self):
        # Independently computed per the spec formula
        h = 2166136261
        for b in b"meshsite":
            h = ((h ^ b) * 16777619) % 2**32
        self.assertEqual(P.fnv1a32(b"meshsite"), h or 1)


class TestCompression(unittest.TestCase):
    def test_roundtrip_raw_deflate(self):
        page = ("# Title\n" + "para " * 200).encode()
        comp = P.deflate(page)
        # raw DEFLATE: no zlib (0x78) or gzip (0x1f 0x8b) header
        self.assertNotEqual(comp[0], 0x78)
        self.assertNotEqual(comp[:2], b"\x1f\x8b")
        self.assertEqual(P.inflate(comp), page)

    def test_bomb_defense(self):
        comp = P.deflate(b"a" * 200000)
        with self.assertRaises(ValueError):
            P.inflate(comp)


class TestRequestParsing(unittest.TestCase):
    def test_roundtrip_get(self):
        raw = P.encode_request(0x1234, P.GET, "/foo?x=1", etag=0xDEADBEEF)
        f = P.parse_request(raw)
        self.assertEqual((f.version, f.req_id, f.method, f.etag, f.target, f.body),
                         (1, 0x1234, P.GET, 0xDEADBEEF, "/foo?x=1", b""))
        self.assertEqual(f.cache_key, raw)

    def test_post_etag_normalized(self):
        raw = P.encode_request(7, P.POST, "/gb", etag=0xDEADBEEF, body=b"msg=hi")
        f = P.parse_request(raw)
        self.assertEqual(f.etag, 0)
        self.assertEqual(f.body, b"msg=hi")
        # cache key identical regardless of the POST etag sent
        raw2 = P.encode_request(7, P.POST, "/gb", etag=0, body=b"msg=hi")
        self.assertEqual(f.cache_key, P.parse_request(raw2).cache_key)

    def assertFrameError(self, payload, code, req_id):
        with self.assertRaises(P.FrameError) as cm:
            P.parse_request(payload)
        self.assertEqual((cm.exception.code, cm.exception.req_id), (code, req_id))

    def test_version_zero_is_error_6_never_3(self):
        raw = bytearray(P.encode_request(9, P.GET, "/"))
        raw[1] = 0
        self.assertFrameError(bytes(raw), P.ERR_UNSUPPORTED_VERSION, 9)
        # even when otherwise malformed (empty path would be ERROR 3)
        self.assertFrameError(bytes([2, 0, 0, 9]), P.ERR_UNSUPPORTED_VERSION, 9)

    def test_id_zero(self):
        raw = P.encode_request(1, P.GET, "/")
        raw = raw[:2] + b"\x00\x00" + raw[4:]
        self.assertFrameError(raw, P.ERR_BAD_REQUEST, 0)

    def test_get_with_body(self):
        raw = P.encode_request(5, P.GET, "/") + b"extra"
        self.assertFrameError(raw, P.ERR_BAD_REQUEST, 5)

    def test_bad_method(self):
        raw = bytearray(P.encode_request(5, P.GET, "/"))
        raw[4] = 9
        self.assertFrameError(bytes(raw), P.ERR_BAD_REQUEST, 5)

    def test_empty_and_truncated_path(self):
        base = struct.pack(">BBHBIB", 2, 1, 5, 0, 0, 0)
        self.assertFrameError(base, P.ERR_BAD_REQUEST, 5)          # path_len 0
        base = struct.pack(">BBHBIB", 2, 1, 5, 0, 0, 10) + b"/ab"
        self.assertFrameError(base, P.ERR_BAD_REQUEST, 5)          # truncated
        base = struct.pack(">BBHBIB", 2, 1, 5, 0, 0, 121) + b"/" * 121
        self.assertFrameError(base, P.ERR_BAD_REQUEST, 5)          # too long

    def test_relative_path(self):
        raw = struct.pack(">BBHBIB", 2, 1, 5, 0, 0, 3) + b"abc"
        self.assertFrameError(raw, P.ERR_BAD_REQUEST, 5)

    def test_unparseable_id_drops_silently(self):
        self.assertFrameError(b"\x02\x01\x00", P.ERR_BAD_REQUEST, None)


class TestChunks(unittest.TestCase):
    def test_single_chunk(self):
        chunks = P.make_chunks(1, 42, b"# Hi\n", P.fnv1a32(b"# Hi\n"))
        self.assertEqual(len(chunks), 1)
        t, v, rid, seq, total, etag = struct.unpack(">BBHBBI", chunks[0][:10])
        self.assertEqual((t, v, rid, seq, total), (P.CHUNK, 1, 42, 0, 1))
        self.assertEqual(P.inflate(chunks[0][10:]), b"# Hi\n")

    def test_multi_chunk_sizes_and_reassembly(self):
        import os
        page = os.urandom(1500)  # incompressible -> multiple chunks
        chunks = P.make_chunks(1, 7, page, 99)
        self.assertGreater(len(chunks), 1)
        for c in chunks[:-1]:
            self.assertEqual(len(c), 200)  # 10 header + 190 data (max payload)
        self.assertLessEqual(len(chunks[-1]), 200)
        blob = b"".join(c[10:] for c in chunks)
        self.assertEqual(P.inflate(blob), page)

    def test_too_large(self):
        import os
        with self.assertRaises(P.FrameError) as cm:
            P.make_chunks(1, 7, os.urandom(5000), 1)
        self.assertEqual(cm.exception.code, P.ERR_TOO_LARGE)


class TestOtherFrames(unittest.TestCase):
    def test_beacon(self):
        self.assertEqual(P.encode_beacon("Base"), b"\x01\x01Base")
        with self.assertRaises(ValueError):
            P.encode_beacon("")
        with self.assertRaises(ValueError):
            P.encode_beacon("x" * 41)
        # control and bidi chars stripped before length check
        self.assertEqual(P.encode_beacon("A‮B\nC"), b"\x01\x01ABC")

    def test_error_frame_truncation(self):
        raw = P.encode_error(0x0102, 4, "é" * 100)  # 200 utf-8 bytes
        self.assertEqual(raw[:4], b"\x04\x01\x02\x04")
        msg = raw[4:]
        self.assertLessEqual(len(msg), 120)
        msg.decode("utf-8")  # no split codepoint

    def test_not_modified(self):
        self.assertEqual(P.encode_not_modified(0x0102, 0xAABBCCDD),
                         b"\x05\x01\x02\xaa\xbb\xcc\xdd")


if __name__ == "__main__":
    unittest.main()
