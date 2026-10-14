import os
import tempfile
import threading
import unittest

from spdw_purge import cats, core, finder


def mk(path, data=b"x" * 4096):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        core.DRY.clear()

    def test_scan_sizes_and_types(self):
        mk(f"{self.d}/a/b/movie.mkv", b"x" * 100000)
        mk(f"{self.d}/a/song.mp3", b"y" * 50000)
        r = core.scan_tree(self.d, lambda *a: None, threading.Event())
        self.assertEqual(r.nfiles, 2)
        self.assertGreaterEqual(r.sizes[self.d], 150000)
        self.assertGreaterEqual(r.types["Video"], 100000)
        self.assertIn("Audio", r.types)
        self.assertTrue(r.top[0][1].endswith("movie.mkv"))

    def test_squarify_covers_area(self):
        rects = core.squarify([(50, "a"), (25, "b"), (15, "c"), (10, "d")], 0, 0, 200, 100)
        self.assertEqual(len(rects), 4)
        self.assertAlmostEqual(sum(w * h for _x, _y, w, h, _p in rects), 20000, places=3)
        for x, y, w, h, _p in rects:
            self.assertGreaterEqual(x, -1e-9)
            self.assertLessEqual(x + w, 200 + 1e-6)
            self.assertLessEqual(y + h, 100 + 1e-6)

    def test_protection(self):
        for p in ("/", "/usr", "/etc", core.HOME):
            self.assertTrue(core.protected(p), p)
        self.assertFalse(core.protected(self.d + "/anything"))
        self.assertTrue(core.dangerous("/etc/passwd"))
        self.assertFalse(core.dangerous(self.d))

    def test_old_kernels_keeps_running_and_newest(self):
        txt = ("linux-image-6.1.0-13-amd64\tii \t300\nlinux-image-6.1.0-18-amd64\tii \t300\n"
               "linux-image-6.1.0-20-amd64\tii \t300\nlinux-headers-6.1.0-13-amd64\tii \t9\n"
               "linux-headers-6.1.0-13-common\tii \t9\nlinux-headers-6.1.0-20-amd64\tii \t9\n"
               "linux-image-amd64\tii \t1\nlinux-image-6.1.0-9-amd64\trc \t0\n")
        old = [n for n, _ in core.pick_old_kernels(txt, "6.1.0-18-amd64")]
        self.assertEqual(old, ["linux-image-6.1.0-13-amd64", "linux-headers-6.1.0-13-amd64",
                               "linux-headers-6.1.0-13-common"])

    def test_dry_run_deletes_nothing(self):
        mk(f"{self.d}/keep/f.bin")
        core.DRY.set()
        try:
            freed = core.destroy(f"{self.d}/keep", False, lambda *a: None)
            self.assertGreater(freed, 0)
            self.assertTrue(os.path.exists(f"{self.d}/keep/f.bin"))
            core.rm_path(f"{self.d}/keep")
            self.assertTrue(os.path.exists(f"{self.d}/keep/f.bin"))
        finally:
            core.DRY.clear()

    def test_real_delete(self):
        mk(f"{self.d}/gone/f.bin")
        self.assertGreater(core.destroy(f"{self.d}/gone", False, lambda *a: None), 0)
        self.assertFalse(os.path.exists(f"{self.d}/gone"))

    def test_helpers(self):
        self.assertEqual(core.human(1536), "1.5K")
        self.assertEqual(core.parse_size("1.5 MB"), 1500000)
        self.assertEqual(core.file_type("X.ISO"), "Disk images")
        self.assertEqual(core.file_type("weird.zzz"), core.TYPE_OTHER)


class FinderTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_duplicates(self):
        blob = os.urandom(200000)
        mk(f"{self.d}/a/one.bin", blob)
        mk(f"{self.d}/b/two.bin", blob)
        mk(f"{self.d}/c/other.bin", os.urandom(200000))   # same size, different content
        mk(f"{self.d}/c/small.txt", b"hi")
        res = finder.find_duplicates(self.d, 1, threading.Event())
        self.assertEqual(len(res), 1)
        self.assertEqual(len(res[0][1]), 2)

    def test_hardlinks_are_not_duplicates(self):
        mk(f"{self.d}/a.bin", os.urandom(100000))
        os.link(f"{self.d}/a.bin", f"{self.d}/b.bin")
        self.assertEqual(finder.find_duplicates(self.d, 1, threading.Event()), [])

    def test_misc(self):
        mk(f"{self.d}/empty.txt", b"")
        os.makedirs(f"{self.d}/void")
        os.symlink(f"{self.d}/nowhere", f"{self.d}/dead")
        res = finder.find_misc(self.d, ["empty_files", "empty_dirs", "broken_links"], threading.Event())
        self.assertEqual(len(res["empty_files"]), 1)
        self.assertEqual(len(res["empty_dirs"]), 1)
        self.assertEqual(len(res["broken_links"]), 1)


class CatTests(unittest.TestCase):
    def test_every_usable_category_scans(self):
        for c in cats.build_cats():
            if c.usable():
                size, _note = c.scan()
                self.assertGreaterEqual(size, 0, c.key)


if __name__ == "__main__":
    unittest.main()
