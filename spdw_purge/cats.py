"""Junk categories: each one knows how to measure itself and how to clean itself."""
import os
import shutil
import stat as S
import time

from . import core
from .core import HOME, IS_ROOT, audit, du, expand, fp_cmd, human, pkg_sizes, protected, rm_path, run_cmd, sh_out


class Cat:
    """level: SAFE (regenerates by itself) / MED (re-downloads or needs care) / AGGR (user data)."""

    def __init__(self, key, title, desc, level, root, scan, run, avail=None):
        self.key, self.title, self.desc, self.level = key, title, desc, level
        self.root, self.scan, self.run = root, scan, run
        self.avail = avail or (lambda: True)

    def usable(self):
        return self.avail() and (IS_ROOT or not self.root)


# a category in the key covers the listed ones (avoid double counting)
COVERED = {"cache_user": {"thumbs", "browser", "dev"}}
# categories that always require typed confirmation
DESTRUCTIVE = {"kernels", "deborphan", "mx_snapshots", "cache_user", "privacy"}


def _freed(log, n):
    log(f"{'would free' if core.DRY.is_set() else 'freed'} ~{human(n)}", "ok")


def _dpkg_purge(names, log):
    if not names:
        log("nothing to do", "dim")
        return
    run_cmd(["dpkg", "--purge"] + names, log)


def _purge_list(names, log):
    if not names:
        log("nothing to do", "dim")
        return
    run_cmd(["apt-get", "purge", "-y"] + names, log)


def build_cats():
    H = HOME
    cats = []

    def path_cat(key, title, desc, level, root, paths, contents=False, avail=None):
        def scan():
            return sum(du(p) for p in expand(paths)), ""

        def run(log):
            freed = 0
            for p in expand(paths):
                if contents and os.path.isdir(p) and not os.path.islink(p):
                    for c in os.listdir(p):
                        freed += rm_path(os.path.join(p, c))
                elif not protected(p):
                    freed += rm_path(p)
            _freed(log, freed)
        cats.append(Cat(key, title, desc, level, root, scan, run, avail))

    # ---- packages (APT)
    cats.append(Cat("apt_cache", "APT CACHE", "downloaded .deb archives", "SAFE", True,
                    lambda: (sum(du(p) for p in expand(["/var/cache/apt/archives/*.deb"])), ""),
                    lambda log: run_cmd(["apt-get", "clean"], log)))

    def apt_auto_scan():
        names = core.apt_sim_removed(["autoremove", "--purge"])
        return pkg_sizes(names), f"{len(names)} packages"
    cats.append(Cat("apt_auto", "APT ORPHANS", "autoremove --purge", "SAFE", True, apt_auto_scan,
                    lambda log: run_cmd(["apt-get", "autoremove", "--purge", "-y"], log)))

    if shutil.which("deborphan"):
        def deb_scan():
            names = sh_out(["deborphan"]).split()
            return pkg_sizes(names), f"{len(names)} libs"
        cats.append(Cat("deborphan", "ORPHAN LIBS", "deborphan", "MED", True, deb_scan,
                        lambda log: _purge_list(sh_out(["deborphan"]).split(), log)))

    def rc_list():
        out = sh_out(["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Abbrev}\n"])
        return [ln.split("\t")[0] for ln in out.splitlines() if "\trc" in ln]
    cats.append(Cat("rc_conf", "RESIDUAL CONFIGS", "removed pkgs with leftover conf (rc)", "SAFE", True,
                    lambda: (0, f"{len(rc_list())} packages"), lambda log: _dpkg_purge(rc_list(), log)))

    def kern_cands():
        txt = sh_out(["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Abbrev}\t${Installed-Size}\n",
                      "linux-image-*", "linux-headers-*"])
        return core.pick_old_kernels(txt, os.uname().release)
    cats.append(Cat("kernels", "OLD KERNELS", "keeps running + newest", "MED", True,
                    lambda: (sum(k for _, k in kern_cands()) * 1024, f"{len(kern_cands())} packages"),
                    lambda log: _purge_list([n for n, _ in kern_cands()], log)))

    # ---- logs / crashes
    cats.append(Cat("journal", "JOURNALD", "vacuum down to 50M", "SAFE", True,
                    lambda: (du("/var/log/journal"), ""),
                    lambda log: run_cmd(["journalctl", "--vacuum-size=50M"], log)))
    path_cat("old_logs", "ROTATED LOGS", "*.gz *.old *.1 in /var/log", "SAFE", True,
             ["/var/log/**/*.gz", "/var/log/**/*.old", "/var/log/**/*.[0-9]",
              "/var/log/**/*-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]"])

    def biglogs():
        return [p for p in expand(["/var/log/*.log", "/var/log/**/*.log", "/var/log/syslog", "/var/log/messages"])
                if os.path.isfile(p) and os.path.getsize(p) > 20 * 1024 * 1024]

    def biglogs_run(log):
        for p in biglogs():
            sz = os.path.getsize(p)
            audit(f"truncate {p} ({human(sz)})")
            if core.DRY.is_set():
                log(f"[dry-run] would truncate {p} ({human(sz)})", "warn")
                continue
            try:
                with open(p, "w"):
                    pass
                log(f"[X] truncated {p} ({human(sz)})", "ok")
            except OSError as ex:
                log(f"[!] {p}: {ex}", "err")
    cats.append(Cat("biglogs", "HUGE ACTIVE LOGS", "truncates live logs > 20M", "MED", True,
                    lambda: (sum(du(p) for p in biglogs()), f"{len(biglogs())} files"), biglogs_run))
    path_cat("crash", "CRASH / COREDUMPS", "/var/crash, systemd coredumps", "SAFE", True,
             ["/var/crash", "/var/lib/systemd/coredump"], contents=True)

    # ---- tmp
    def tmp_cands():
        out, lim = [], time.time() - 86400
        keep = (".X11-unix", ".ICE-unix", ".font-unix", ".XIM-unix", ".Test-unix",
                "systemd-private", "ssh-", "tmux-", "snap-private")
        for base in ("/tmp", "/var/tmp"):
            try:
                for e in os.scandir(base):
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if e.name.startswith(keep) or S.S_ISSOCK(s.st_mode) or max(s.st_mtime, s.st_atime) > lim:
                        continue
                    out.append(e.path)
            except OSError:
                pass
        return out

    def tmp_run(log):
        f = sum(rm_path(p) for p in tmp_cands())
        _freed(log, f)
    cats.append(Cat("tmp", "STALE TMP", "/tmp and /var/tmp older than 24h", "MED", True,
                    lambda: (sum(du(p) for p in tmp_cands()), ""), tmp_run))

    # ---- containers / snapshots
    for exe, label in (("docker", "DOCKER"), ("podman", "PODMAN")):
        if shutil.which(exe):
            cats.append(Cat(exe, f"{label} PRUNE", "stopped containers, dangling images", "MED", True,
                            lambda: (0, "n/a"),
                            lambda log, e=exe: run_cmd([e, "system", "prune", "-f"], log),
                            avail=lambda e=exe: bool(shutil.which(e))))
    path_cat("mx_snapshots", "MX SNAPSHOTS", "ISO snapshots in /home/snapshot", "AGGR", True,
             ["/home/snapshot/snapshot-*.iso", "/home/snapshot/snapshot-*.md5", "/home/snapshot/snapshot-*.sha256"])

    # ---- user space
    path_cat("trash", "TRASH", "~/.local/share/Trash", "SAFE", False,
             [f"{H}/.local/share/Trash/files", f"{H}/.local/share/Trash/info"], contents=True)
    path_cat("thumbs", "THUMBNAILS", "~/.cache/thumbnails", "SAFE", False,
             [f"{H}/.cache/thumbnails"], contents=True)
    path_cat("browser", "BROWSER CACHE", "firefox / chrome / chromium / brave", "SAFE", False,
             [f"{H}/.cache/{b}" for b in ("mozilla", "google-chrome", "chromium",
                                          "BraveSoftware", "vivaldi", "opera")], contents=True)
    path_cat("dev", "DEV CACHES", "gradle, npm, cargo, pip, android", "MED", False,
             [f"{H}/.gradle/caches", f"{H}/.npm/_cacache", f"{H}/.cargo/registry/cache",
              f"{H}/.cache/pip", f"{H}/.android/cache"], contents=True)
    path_cat("privacy", "PRIVACY TRACES", "shell history, recent files", "MED", False,
             [f"{H}/.bash_history", f"{H}/.zsh_history", f"{H}/.python_history", f"{H}/.lesshst",
              f"{H}/.local/share/recently-used.xbel"])
    path_cat("cache_user", "ALL OF ~/.cache", "wipes EVERYTHING (includes above)", "AGGR", False,
             [f"{H}/.cache"], contents=True)

    if shutil.which("flatpak"):
        def fp_run(log):
            run_cmd(["flatpak", "uninstall", "--unused", "-y", "--system"], log)
            run_cmd(fp_cmd("user", ["uninstall", "--unused", "-y"]), log)
        cats.append(Cat("flatpak", "FLATPAK UNUSED", "unused runtimes", "SAFE", True,
                        lambda: (0, "n/a"), fp_run))
    return cats
