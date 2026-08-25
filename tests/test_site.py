import tempfile
import unittest
from pathlib import Path

from meshsites import protocol as P
from meshsites.site import Site, PageError


def req(target, method=P.GET, body=b"", etag=0):
    return P.parse_request(P.encode_request(0x1111, method, target,
                                            etag=etag, body=body))


class TestSite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.site = Site(self.root, "Test")
        (self.root / "index.md").write_text("# Home\n")
        (self.root / "about.md").write_text("# About\n")
        (self.root / "empty.md").write_text("")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "page.md").write_text("# Sub\n")
        (self.root / ".secret.md").write_text("# hidden\n")
        (self.root / "echo.py").write_text(
            "def handle(req):\n"
            "    return '# %s %s %s\\n' % (req.method, req.path,\n"
            "                              sorted(req.args.items()))\n")
        (self.root / "boom.py").write_text(
            "def handle(req):\n    raise RuntimeError('kaboom')\n")
        (self.root / "notext.py").write_text(
            "def handle(req):\n    return 42\n")
        (self.root / "twin.py").write_text("def handle(req):\n    return '# py\\n'\n")
        (self.root / "twin.md").write_text("# md\n")

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, *a, **kw):
        return self.site.render(req(*a, **kw), 0xAABBCCDD, "!aabbccdd")

    def assertError(self, code, *a, **kw):
        with self.assertRaises(PageError) as cm:
            self.render(*a, **kw)
        self.assertEqual(cm.exception.code, code)

    def test_root_maps_to_index(self):
        self.assertEqual(self.render("/"), "# Home\n")

    def test_static_and_nested(self):
        self.assertEqual(self.render("/about"), "# About\n")
        self.assertEqual(self.render("/sub/page"), "# Sub\n")

    def test_not_found_and_empty(self):
        self.assertError(P.ERR_NOT_FOUND, "/missing")
        self.assertError(P.ERR_NOT_FOUND, "/empty")

    def test_traversal_rejected_after_percent_decoding(self):
        self.assertError(P.ERR_BAD_REQUEST, "/../etc/passwd")
        self.assertError(P.ERR_BAD_REQUEST, "/%2e%2e/etc/passwd")
        self.assertError(P.ERR_BAD_REQUEST, "/sub/%2E%2E/%2e%2e/x")

    def test_hidden_files_not_served(self):
        self.assertError(P.ERR_NOT_FOUND, "/.secret")

    def test_dynamic_get_query(self):
        page = self.render("/echo?a=1&b=hi%20there")
        self.assertEqual(page, "# GET /echo [('a', '1'), ('b', 'hi there')]\n")

    def test_dynamic_post_form(self):
        page = self.render("/echo", method=P.POST, body=b"a=2&msg=yo")
        self.assertEqual(page, "# POST /echo [('a', '2'), ('msg', 'yo')]\n")

    def test_py_takes_precedence_over_md(self):
        self.assertEqual(self.render("/twin"), "# py\n")

    def test_handler_exception_is_error_4(self):
        self.assertError(P.ERR_SERVER_ERROR, "/boom")
        self.assertError(P.ERR_SERVER_ERROR, "/notext")

    def test_handler_reload_on_change(self):
        import os, time
        self.assertEqual(self.render("/twin"), "# py\n")
        p = self.root / "twin.py"
        p.write_text("def handle(req):\n    return '# py2\\n'\n")
        os.utime(p, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        self.assertEqual(self.render("/twin"), "# py2\n")


if __name__ == "__main__":
    unittest.main()
