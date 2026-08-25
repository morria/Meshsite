import tempfile
import unittest
from pathlib import Path

from meshsites.store import Store, sanitize


class TestSanitize(unittest.TestCase):
    def test_strips_newlines_and_controls(self):
        self.assertEqual(sanitize("a\nb\rc\x00d\x1be"), "a b cde")

    def test_strips_bidi_overrides(self):
        self.assertEqual(sanitize("a‮b⁦c"), "abc")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "s.log", max_bytes=400)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_with_injection_attempt(self):
        self.store.append({"who": "eve\nmsg=fake", "msg": "hi & bye = ok?"})
        [e] = self.store.entries()
        self.assertEqual(e["who"], "eve msg=fake")   # newline neutralized
        self.assertEqual(e["msg"], "hi & bye = ok?")  # urlencoding roundtrips

    def test_oldest_dropped_at_cap(self):
        for i in range(50):
            self.store.append({"n": str(i)})
        entries = self.store.entries()
        self.assertLess(len(entries), 50)
        self.assertEqual(entries[-1]["n"], "49")     # newest kept
        nums = [int(e["n"]) for e in entries]
        self.assertEqual(nums, sorted(nums))          # oldest-first order intact


if __name__ == "__main__":
    unittest.main()
