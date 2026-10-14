"""Finders: duplicate files, empty files/dirs, broken symlinks, big & old files."""
import hashlib
import os
import stat as S
import time

from .core import SKIP

PARTIAL = 64 * 1024


def _walk(root, stop):
    """Yield (dir, [DirEntry]) staying on one filesystem."""
    root = os.path.normpath(root)
    try:
        dev0 = os.lstat(root).st_dev
    except OSError:
        return
    stack = [root]
    while stack:
        if stop.is_set():
            return
        d = stack.pop()
        try:
            ents = list(os.scandir(d))
        except OSError:
            continue
        yield d, ents
        for e in ents:
            try:
                if e.is_dir(follow_symlinks=False) and e.path not in SKIP \
                        and e.stat(follow_symlinks=False).st_dev == dev0:
                    stack.append(e.path)
            except OSError:
                pass


def _hash(path, limit=None, stop=None):
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    if stop is not None and stop.is_set():
                        return None
                    h.update(chunk)
    except OSError:
        return None
    return h.digest()


def _age_key(p):
    try:
        return (os.lstat(p).st_mtime, len(p))
    except OSError:
        return (0, len(p))


def find_duplicates(root, min_size, stop, cb=lambda s: None):
    """-> [(size, [paths oldest-first])] sorted by wasted space. 3 stages: size, head hash, full hash."""
    by_size, seen, n = {}, set(), 0
    for _d, ents in _walk(root, stop):
        for e in ents:
            try:
                s = e.stat(follow_symlinks=False)
                if not S.S_ISREG(s.st_mode) or s.st_size < max(min_size, 1):
                    continue
                k = (s.st_dev, s.st_ino)
                if k in seen:  # hardlinks are the same data, not duplicates
                    continue
                seen.add(k)
                by_size.setdefault(s.st_size, []).append(e.path)
                n += 1
            except OSError:
                pass
        if n % 500 == 0:
            cb(f"indexing... {n} files")
    cands = {sz: ps for sz, ps in by_size.items() if len(ps) > 1}
    out, done = [], 0
    for sz, paths in cands.items():
        if stop.is_set():
            return None
        done += 1
        cb(f"hashing {done}/{len(cands)} size classes")
        heads = {}
        for p in paths:
            h = _hash(p, PARTIAL)
            if h is not None:
                heads.setdefault(h, []).append(p)
        for group in heads.values():
            if len(group) < 2:
                continue
            if sz <= PARTIAL:
                full = {None: group}
            else:
                full = {}
                for p in group:
                    h = _hash(p, None, stop)
                    if h is not None:
                        full.setdefault(h, []).append(p)
            for g in full.values():
                if len(g) > 1:
                    g.sort(key=_age_key)
                    out.append((sz, g))
    out.sort(key=lambda t: t[0] * (len(t[1]) - 1), reverse=True)
    return out


def find_misc(root, kinds, stop, min_big=500 << 20, older_days=180, cb=lambda s: None):
    """kinds: subset of {'empty_files','empty_dirs','broken_links','old_large'} -> {kind: [(size, path)]}"""
    res = {k: [] for k in kinds}
    lim = time.time() - older_days * 86400
    n = 0
    for d, ents in _walk(root, stop):
        n += 1
        if n % 300 == 0:
            cb(f"scanning... {n} folders")
        if "empty_dirs" in res and not ents and os.path.normpath(d) != os.path.normpath(root):
            res["empty_dirs"].append((0, d))
        for e in ents:
            try:
                if e.is_symlink():
                    if "broken_links" in res and not os.path.exists(e.path):
                        res["broken_links"].append((0, e.path))
                    continue
                s = e.stat(follow_symlinks=False)
                if S.S_ISREG(s.st_mode):
                    if "empty_files" in res and s.st_size == 0:
                        res["empty_files"].append((0, e.path))
                    if "old_large" in res and s.st_blocks * 512 >= min_big and s.st_mtime < lim:
                        res["old_large"].append((s.st_blocks * 512, e.path))
            except OSError:
                pass
    for v in res.values():
        v.sort(reverse=True)
    return None if stop.is_set() else res
