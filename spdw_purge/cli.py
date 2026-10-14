"""Command line entry point. With no flags the GUI starts."""
import argparse
import os
import sys
import threading

from . import core
from .cats import COVERED, DESTRUCTIVE, build_cats
from .core import APP, VER, human


def _log(msg, tag="norm"):
    print(msg)


def parser():
    ap = argparse.ArgumentParser(
        prog="spdw-purge",
        description=f"{APP} v{VER} - disk analyzer & cleaner. No flags = GUI.",
        epilog="by sirpips · SPDW Factory Lab")
    ap.add_argument("--version", action="version", version=f"{APP} {VER}")
    ap.add_argument("--list", action="store_true", help="measure and list junk categories")
    ap.add_argument("--clean", metavar="WHAT", help="'safe', 'all' or comma-separated category keys")
    ap.add_argument("--top", metavar="PATH", help="print the biggest folders and files under PATH")
    ap.add_argument("--dry-run", action="store_true", help="never modify anything, just report")
    ap.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--user", action="store_true", help="(internal) unprivileged GUI mode")
    ap.add_argument("--selftest", action="store_true", help="run built-in sanity checks")
    return ap


def cmd_list(cats):
    print(f"{'KEY':<14}{'LEVEL':<6}{'SIZE':>10}  DESCRIPTION")
    tot = 0
    for c in cats:
        if not c.usable():
            print(f"{c.key:<14}{c.level:<6}{'(root)' if c.root and not core.IS_ROOT else 'n/a':>10}  {c.desc}")
            continue
        try:
            sz, note = c.scan()
        except Exception as ex:  # noqa: BLE001
            sz, note = 0, f"err {ex}"
        if c.level == "SAFE":
            tot += sz
        print(f"{c.key:<14}{c.level:<6}{human(sz):>10}  {c.desc} {note}")
    print(f"\nreclaimable with --clean safe: ~{human(tot)}")


def cmd_clean(cats, what, yes):
    what = what.lower()
    if what == "safe":
        sel = [c for c in cats if c.usable() and c.level == "SAFE"]
    elif what == "all":
        sel = [c for c in cats if c.usable() and c.key not in DESTRUCTIVE]
    else:
        keys = {k.strip() for k in what.split(",")}
        sel = [c for c in cats if c.key in keys and c.usable()]
        missing = keys - {c.key for c in sel}
        if missing:
            print("unknown or unavailable categories:", ", ".join(sorted(missing)), file=sys.stderr)
            return 2
    if not sel:
        print("nothing selected.")
        return 0
    print("categories:", ", ".join(c.key for c in sel))
    if core.DRY.is_set():
        print("(dry-run: nothing will be changed)")
    if not yes and not core.DRY.is_set():
        if input("type YES to proceed: ").strip() != "YES":
            print("aborted.")
            return 1
    skip = set()
    for k, cov in COVERED.items():
        if k in {c.key for c in sel}:
            skip |= cov
    for c in sel:
        if c.key in skip:
            continue
        print(f">> {c.title}")
        c.run(_log)
    return 0


def cmd_top(path, n=20):
    path = os.path.expanduser(path)
    print(f"scanning {path} ...")
    res = core.scan_tree(path, lambda *a: None, threading.Event())
    root = os.path.normpath(path)
    print(f"\n{human(res.sizes.get(root, 0))} in {res.nfiles} files\n\nFOLDERS")
    for sz, p, _n, isdir in core.children_sizes(root, res.sizes, n):
        print(f"  {human(sz):>8}  {'d' if isdir else 'f'}  {p}")
    print("\nTYPES")
    for t, sz in sorted(res.types.items(), key=lambda kv: -kv[1]):
        print(f"  {human(sz):>8}  {t}")
    print("\nFILES")
    for sz, p in res.top[:n]:
        print(f"  {human(sz):>8}  {p}")


def selftest():
    import tempfile

    from . import finder
    from .core import destroy, parse_size, pick_old_kernels, protected, scan_tree, squarify
    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/a/b")
    open(f"{d}/a/b/f1.mkv", "wb").write(b"x" * 100000)
    open(f"{d}/a/f2.mkv", "wb").write(b"x" * 100000)
    res = scan_tree(d, lambda *a: None, threading.Event())
    assert res.nfiles == 2 and res.types.get("Video", 0) >= 200000, res.types
    assert protected("/") and protected("/usr") and not protected(d + "/a")
    dup = finder.find_duplicates(d, 1, threading.Event())
    assert len(dup) == 1 and len(dup[0][1]) == 2, dup
    rects = squarify([(6, "a"), (3, "b"), (1, "c")], 0, 0, 100, 50)
    assert abs(sum(w * h for _x, _y, w, h, _p in rects) - 5000) < 1e-6
    old = pick_old_kernels("linux-image-6.1.0-13-amd64\tii \t3\nlinux-image-6.1.0-18-amd64\tii \t3\n"
                           "linux-image-6.1.0-20-amd64\tii \t3\n", "6.1.0-18-amd64")
    assert [n for n, _ in old] == ["linux-image-6.1.0-13-amd64"], old
    core.DRY.set()
    assert destroy(f"{d}/a", False, _log) > 0 and os.path.exists(f"{d}/a")
    core.DRY.clear()
    assert destroy(f"{d}/a", False, _log) > 0 and not os.path.exists(f"{d}/a")
    assert human(1536) == "1.5K" and parse_size("1.5 MB") == 1500000
    for c in build_cats():
        if c.usable():
            c.scan()
    print("selftest OK")


def main(argv):
    args = parser().parse_args(argv)
    if args.dry_run:
        core.DRY.set()
    if args.selftest:
        selftest()
        return 0
    if args.list:
        cmd_list(build_cats())
        return 0
    if args.clean:
        return cmd_clean(build_cats(), args.clean, args.yes)
    if args.top:
        cmd_top(args.top)
        return 0
    try:
        from . import gui
    except ImportError as ex:
        print(f"GUI unavailable ({ex}). Install python3-tk or use --list / --clean / --top.", file=sys.stderr)
        return 1
    gui.run()
    return 0
