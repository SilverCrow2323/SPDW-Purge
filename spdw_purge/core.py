"""SPDW Purge - core logic (no GUI imports).

Disk scanning, safe deletion, package helpers, treemap layout, audit log.
"""
import glob
import heapq
import os
import pwd
import re
import shutil
import stat as S
import subprocess
import threading
import time
from collections import namedtuple

APP = "SPDW Purge"
VER = "1.1.0"
AUTHOR = "sirpips · SPDW Factory Lab"
URL = "https://github.com/sirpips/spdw-purge"

IS_ROOT = os.geteuid() == 0

# Global dry-run switch: when set, nothing is ever modified.
DRY = threading.Event()


def _real_user():
    uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
    try:
        return pwd.getpwuid(int(uid)) if uid else pwd.getpwuid(os.geteuid())
    except (KeyError, ValueError):
        return pwd.getpwuid(os.geteuid())


_RU = _real_user()
HOME = _RU.pw_dir
USER = _RU.pw_name
UID = _RU.pw_uid
ENV = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C")

SKIP = {"/proc", "/sys", "/dev", "/run"}
PROTECTED = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64",
    "/libx32", "/proc", "/root", "/run", "/sbin", "/sys", "/usr", "/var", "/opt",
    "/srv", "/mnt", "/media", "/tmp", "/usr/bin", "/usr/lib", "/usr/sbin",
    "/usr/share", "/usr/local", "/var/lib", "/var/log", "/var/cache",
    "/lost+found", os.path.dirname(HOME), HOME,
}
DANGER_PREFIX = ("/usr", "/etc", "/boot", "/bin", "/sbin", "/lib", "/var/lib",
                 "/opt", os.path.join(HOME, ".ssh"), os.path.join(HOME, ".gnupg"))
CRITICAL_PKG = re.compile(
    r"^(xorg|xserver-xorg|lightdm|xfce4|xfwm4|xfdesktop4|network-manager|systemd|"
    r"apt|dpkg|libc6|grub|linux-image-amd64|sudo|mx-|python3$|python3-tk|"
    r"spdw-purge|policykit|pkexec|polkitd|initramfs-tools|udev|dbus)")

STATE_DIR = os.path.join(HOME, ".local", "state", "spdw-purge")
AUDIT_FILE = os.path.join(STATE_DIR, "actions.log")

# file-type buckets for the "TYPES" view
TYPE_EXT = {
    "Video": "mp4 mkv avi mov webm m4v wmv flv mpg mpeg ts",
    "Audio": "mp3 flac wav ogg m4a aac opus wma",
    "Images": "jpg jpeg png gif bmp webp svg tif tiff heic raw cr2 nef xcf psd",
    "Archives": "zip tar gz xz bz2 7z rar zst tgz lz4",
    "Disk images": "iso img qcow2 vdi vmdk vhd vhdx ova",
    "Games / ROMs": "rvz wbfs gcz gcm nsp xci chd cue nds gba gbc sfc smc nes z64 n64 v64 pbp cso",
    "Documents": "pdf doc docx odt xls xlsx ods ppt pptx txt epub md csv",
    "Packages": "deb rpm apk appimage flatpak",
    "Code / Dev": "py js c h cpp o so a java class jar rs go lua sh json xml html css pyc",
    "Logs / Cache": "log journal tmp cache bak old swp",
}
_EXT2TYPE = {e: t for t, exts in TYPE_EXT.items() for e in exts.split()}
TYPE_OTHER = "Other"

ScanResult = namedtuple("ScanResult", "sizes top nfiles types")


# ----------------------------------------------------------------- helpers
def human(n):
    n = float(n)
    for u in ("B", "K", "M", "G", "T"):
        if n < 1024 or u == "T":
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024


def bar(frac, w=10):
    frac = max(0.0, min(1.0, frac))
    k = int(round(frac * w))
    return "█" * k + "░" * (w - k) + f" {frac * 100:5.1f}%"


def parse_size(s):
    m = re.match(r"\s*([\d.,]+)\s*([kKMGT]?)B?", s or "")
    if not m:
        return 0
    v = float(m.group(1).replace(",", "."))
    return int(v * {"": 1, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[m.group(2)])


def audit(msg):
    """Append one line to the per-user audit log (best effort)."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + ("[DRY] " if DRY.is_set() else "") + msg + "\n")
        if IS_ROOT:
            for p in (STATE_DIR, AUDIT_FILE):
                os.chown(p, UID, _RU.pw_gid)
    except OSError:
        pass


def sh_out(cmd, env=None):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, env=env or ENV, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def run_cmd(cmd, log):
    """Run a command, streaming output to `log`. Honors dry-run. Returns exit code."""
    line = " ".join(cmd)
    if DRY.is_set():
        log(f"[dry-run] $ {line}", "warn")
        audit("$ " + line)
        return 0
    log("$ " + line, "dim")
    audit("$ " + line)
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=ENV, errors="replace")
        for ln in p.stdout:
            ln = ln.rstrip()
            if ln and not re.match(r"^\(Reading database \.\.\. \d+%", ln):
                log(ln, "norm")
        p.wait()
        if p.returncode:
            log(f"exit code {p.returncode}", "err")
        return p.returncode
    except OSError as ex:
        log(f"error: {ex}", "err")
        return 1


def du(path):
    """Real disk usage (allocated blocks) of a file or tree."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not S.S_ISDIR(st.st_mode):
        return st.st_blocks * 512
    total, seen, stack = 0, set(), [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        s = e.stat(follow_symlinks=False)
                        if S.S_ISDIR(s.st_mode):
                            stack.append(e.path)
                        elif s.st_nlink > 1:
                            k = (s.st_dev, s.st_ino)
                            if k in seen:
                                continue
                            seen.add(k)
                        total += s.st_blocks * 512
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def file_type(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return _EXT2TYPE.get(ext, TYPE_OTHER)


def scan_tree(root, cb, stop):
    """Iterative walk that never crosses filesystems. Returns ScanResult or None if stopped."""
    root = os.path.normpath(root)
    try:
        dev0 = os.lstat(root).st_dev
    except OSError:
        return ScanResult({}, [], 0, {})
    sizes, top, seen, dirs, n, types = {}, [], set(), [], 0, {}
    stack = [root]
    while stack:
        if stop.is_set():
            return None
        d = stack.pop()
        dirs.append(d)
        tot = 0
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        s = e.stat(follow_symlinks=False)
                        if S.S_ISDIR(s.st_mode):
                            if s.st_dev == dev0 and e.path not in SKIP:
                                stack.append(e.path)
                                tot += s.st_blocks * 512
                            continue
                        if s.st_nlink > 1:
                            k = (s.st_dev, s.st_ino)
                            if k in seen:
                                continue
                            seen.add(k)
                        sz = s.st_blocks * 512
                        tot += sz
                        n += 1
                        if S.S_ISREG(s.st_mode):
                            t = file_type(e.name)
                            types[t] = types.get(t, 0) + sz
                            item = (sz, e.path)
                            if len(top) < 300:
                                heapq.heappush(top, item)
                            elif sz > top[0][0]:
                                heapq.heappushpop(top, item)
                    except OSError:
                        pass
        except OSError:
            pass
        sizes[d] = tot
        if len(dirs) % 400 == 0:
            cb(d, n, len(dirs))
    for d in sorted(dirs, key=len, reverse=True):
        if d != root:
            p = os.path.dirname(d)
            sizes[p] = sizes.get(p, 0) + sizes[d]
    return ScanResult(sizes, sorted(top, reverse=True), n, types)


def children_sizes(path, sizes, limit=None):
    """[(size, path, name, is_dir)] of the direct children of `path`, biggest first."""
    rows = []
    try:
        for e in os.scandir(path):
            try:
                s = e.stat(follow_symlinks=False)
            except OSError:
                continue
            isdir = S.S_ISDIR(s.st_mode)
            sz = sizes.get(e.path, 0) if isdir else s.st_blocks * 512
            rows.append((sz, e.path, e.name, isdir))
    except OSError:
        pass
    rows.sort(reverse=True)
    return rows[:limit] if limit else rows


def squarify(items, x, y, w, h):
    """Squarified treemap. items: [(value, payload)] sorted desc. -> [(x, y, w, h, payload)]."""
    items = [(v, p) for v, p in items if v > 0]
    total = sum(v for v, _ in items)
    out = []
    if total <= 0 or w <= 0 or h <= 0:
        return out
    k = w * h / total
    rest = [(v * k, p) for v, p in items]

    def worst(row, side):
        s = sum(v for v, _ in row)
        return max(max(side * side * v / (s * s), (s * s) / (side * side * v)) for v, _ in row)

    while rest:
        side = min(w, h)
        row = [rest.pop(0)]
        while rest and worst(row + [rest[0]], side) <= worst(row, side):
            row.append(rest.pop(0))
        s = sum(v for v, _ in row)
        if w >= h:
            rw, yy = s / h, y
            for v, p in row:
                hh = v / rw
                out.append((x, yy, rw, hh, p))
                yy += hh
            x, w = x + rw, w - rw
        else:
            rh, xx = s / w, x
            for v, p in row:
                ww = v / rh
                out.append((xx, y, ww, rh, p))
                xx += ww
            y, h = y + rh, h - rh
    return out


# ------------------------------------------------------------ safe deletion
def protected(p):
    cands = {os.path.normpath(p)}
    if not os.path.islink(p):
        cands.add(os.path.realpath(p))
    for q in cands:
        if q in PROTECTED or os.path.ismount(q) or q.startswith(("/proc", "/sys", "/dev")):
            return True
    return False


def dangerous(p):
    q = os.path.normpath(p)
    return any(q == d or q.startswith(d + "/") for d in DANGER_PREFIX)


def rm_path(p):
    """Delete a file/tree silently. Returns bytes freed (or would-be freed in dry-run)."""
    n = du(p)
    audit(f"rm {p} ({human(n)})")
    if DRY.is_set():
        return n
    try:
        if os.path.islink(p) or not os.path.isdir(p):
            os.remove(p)
        else:
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        return 0
    return n


def destroy(p, shred, log):
    """Delete with logging and optional shred. Returns bytes freed."""
    freed = du(p)
    audit(f"{'shred' if shred else 'rm'} {p} ({human(freed)})")
    if DRY.is_set():
        log(f"[dry-run] would delete {p}  ({human(freed)})", "warn")
        return freed
    try:
        if os.path.islink(p) or os.path.isfile(p):
            if shred and os.path.isfile(p) and not os.path.islink(p):
                r = subprocess.run(["shred", "-u", "-n", "1", p], capture_output=True, text=True)
                if r.returncode:
                    raise OSError(r.stderr.strip() or "shred failed")
            else:
                os.remove(p)
        elif os.path.isdir(p):
            if shred:
                for dp, _dn, fn in os.walk(p):
                    for f in fn:
                        fp = os.path.join(dp, f)
                        if os.path.isfile(fp) and not os.path.islink(fp):
                            subprocess.run(["shred", "-u", "-n", "1", fp], capture_output=True)
            shutil.rmtree(p)
        log(f"[X] {p}  ({human(freed)})", "ok")
        return freed
    except Exception as ex:  # noqa: BLE001
        log(f"[!] {p}: {ex}", "err")
        return 0


def expand(paths):
    out = set()
    for pat in paths:
        out.update(glob.glob(pat, recursive=True))
    return sorted(out)


# ------------------------------------------------------------ packages
def pkg_sizes(names):
    if not names:
        return 0
    out = sh_out(["dpkg-query", "-W", "-f", "${Installed-Size}\t${Package}\n"] + list(names))
    tot = 0
    for ln in out.splitlines():
        try:
            tot += int(ln.split("\t")[0]) * 1024
        except ValueError:
            pass
    return tot


def apt_sim_removed(args):
    out = sh_out(["apt-get", "-s"] + args)
    return re.findall(r"^(?:Remv|Purg) (\S+)", out, re.M)


def _kkey(tag):
    m = re.match(r"(\d+(?:\.\d+)*-\d+)", tag)
    return m.group(1) if m else tag


def pick_old_kernels(dpkg_text, cur):
    """dpkg_text: lines 'pkg<TAB>status<TAB>kb'. Keeps the running + newest kernel."""
    rows = []
    for ln in dpkg_text.splitlines():
        p = ln.split("\t")
        if len(p) < 3 or not p[1].startswith("ii"):
            continue
        m = re.match(r"^linux-(image|headers)-(\d.*)$", p[0])
        if m:
            rows.append((p[0], m.group(1), m.group(2), int(p[2] or 0)))
    img_keys = {_kkey(t) for _, k, t, _ in rows if k == "image"}
    keep = {_kkey(cur)}
    if img_keys:
        keep.add(max(img_keys, key=lambda k: [int(x) for x in re.findall(r"\d+", k)]))
    return [(n, kb) for n, k, t, kb in rows if _kkey(t) not in keep]


def fp_cmd(scope, args):
    cmd = ["flatpak", args[0], "--" + scope] + args[1:]
    if scope == "user" and IS_ROOT and USER != "root":
        return ["runuser", "-u", USER, "--", "env", f"HOME={HOME}"] + cmd
    return cmd


def flatpak_list():
    out = []
    if not shutil.which("flatpak"):
        return out
    for scope in ("system", "user"):
        if scope == "user" and IS_ROOT and USER == "root":
            continue
        txt = sh_out(fp_cmd(scope, ["list", "--app", "--columns=application,name,size"]))
        for ln in txt.splitlines():
            p = ln.split("\t")
            if len(p) >= 3 and "." in p[0]:
                out.append(dict(iid=f"fp|{scope}|{p[0]}", name=f"{p[1]} [{p[0]}]", size=parse_size(p[2]),
                                ver=f"flatpak/{scope}", desc=p[0], prot=False, kind="fp",
                                scope=scope, app=p[0]))
    return out


def load_dpkg():
    fmt = ("${Installed-Size}\t${binary:Package}\t${Version}\t${Essential}\t${Priority}\t"
           "${db:Status-Abbrev}\t${binary:Summary}\n")
    apps = []
    for ln in sh_out(["dpkg-query", "-W", "-f", fmt]).splitlines():
        p = ln.split("\t")
        if len(p) < 7 or not p[5].startswith("ii"):
            continue
        try:
            kb = int(p[0])
        except ValueError:
            kb = 0
        apps.append(dict(iid=p[1], name=p[1], size=kb * 1024, ver=p[2], desc=p[6],
                         prot=(p[3] == "yes" or p[4] == "required"), kind="deb"))
    return apps


def residues_for(pkgs):
    """Leftover config/cache dirs of packages that are no longer installed."""
    found = []
    for pkg in pkgs:
        base = pkg.split(":")[0]
        st = sh_out(["dpkg-query", "-W", "-f", "${db:Status-Abbrev}", base]).strip()
        if st.startswith("ii"):
            continue
        cand = [f"{HOME}/.config/{base}", f"{HOME}/.cache/{base}", f"{HOME}/.local/share/{base}",
                f"{HOME}/.local/state/{base}", f"{HOME}/.{base}"]
        if IS_ROOT:
            cand += [f"/etc/{base}", f"/var/lib/{base}", f"/var/cache/{base}", f"/var/log/{base}"]
        for c in cand:
            if os.path.lexists(c) and not protected(c):
                found.append((c, du(c)))
    return found


def open_in_file_manager(path):
    """Open the folder in the desktop file manager, as the real (non-root) user."""
    target = path if os.path.isdir(path) else os.path.dirname(path)
    cmd = ["xdg-open", target]
    if IS_ROOT and USER != "root":
        cmd = ["runuser", "-u", USER, "--", "env", f"HOME={HOME}",
               f"DISPLAY={os.environ.get('DISPLAY', ':0')}",
               f"XDG_RUNTIME_DIR=/run/user/{UID}",
               f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{UID}/bus"] + cmd
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def write_report(path, scan, root, junk=None):
    """Write a Markdown report of a scan (+ optional junk sizes)."""
    tot = scan.sizes.get(root, 0)
    L = [f"# {APP} report", "", f"- target: `{root}`", f"- total: **{human(tot)}** in {scan.nfiles} files",
         f"- generated: {time.strftime('%Y-%m-%d %H:%M')}  ·  {AUTHOR}", "", "## Biggest folders", ""]
    for sz, p, name, isdir in children_sizes(root, scan.sizes, 25):
        L.append(f"- {human(sz):>8}  {'📁' if isdir else '📄'} `{p}`")
    L += ["", "## By file type", ""]
    for t, sz in sorted(scan.types.items(), key=lambda kv: -kv[1]):
        L.append(f"- {human(sz):>8}  {t}")
    L += ["", "## Top 50 files", ""]
    L += [f"- {human(sz):>8}  `{p}`" for sz, p in scan.top[:50]]
    if junk:
        L += ["", "## Reclaimable junk", ""] + [f"- {human(v):>8}  {k}" for k, v in junk.items()]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    if IS_ROOT:
        try:
            os.chown(path, UID, _RU.pw_gid)
        except OSError:
            pass
    return path
