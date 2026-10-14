#!/usr/bin/env python3
"""Generate the README screenshots headlessly (Xvfb + ImageMagick `import`).

Builds a throw-away demo home (fallocate'd files, never your real data), drives the real GUI
and captures every tab.

    xvfb-run -a -s "-screen 0 1280x900x24" python3 tools/screenshots.py OUTDIR [--home /home/demo]
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MB = 1 << 20
SCALE = 0.35      # keep the demo small on disk
OLD = time.time() - 2.2 * 365 * 86400


def mk(path, mb, tag=None, old=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    size = max(int(mb * SCALE * MB), 64)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.posix_fallocate(fd, 0, size)
    except OSError:
        os.ftruncate(fd, size)
    os.pwrite(fd, (tag or path).encode()[:480].ljust(16, b"\0"), 0)
    os.close(fd)
    if old:
        os.utime(path, (OLD, OLD))


def build_demo(home):
    H = home
    for n, mb in (("holiday-2024.mkv", 640), ("concert-live.mp4", 380), ("tutorial-blender.webm", 120),
                  ("timelapse.mov", 210)):
        mk(f"{H}/Videos/{n}", mb)
    mk(f"{H}/ISOs/mx-23.4-xfce_x64.iso", 1100, old=True)
    mk(f"{H}/ISOs/debian-12-netinst.iso", 420, old=True)
    mk(f"{H}/ISOs/ventoy-1.0.99-linux.tar.gz", 18)
    for n, mb in (("Metroid Prime (USA).rvz", 780), ("Wind Waker.rvz", 640), ("Super Mario Sunshine.rvz", 520),
                  ("Melee.rvz", 460)):
        mk(f"{H}/Games/ROMs/gamecube/{n}", mb)
    mk(f"{H}/Games/ROMs/psx/FF7 disc1.chd", 330)
    for i, n in enumerate(("Minish Cap", "Advance Wars", "Metroid Fusion", "Pokemon Emerald", "Golden Sun")):
        mk(f"{H}/Games/ROMs/gba/{n}.gba", 16 + 4 * i)
    for i in range(6):
        mk(f"{H}/Games/ROMs/snes/game{i}.sfc", 2 + i)
    mk(f"{H}/Downloads/backup-2023.zip", 150, "bk23", old=True)
    mk(f"{H}/Documents/Archive/backup-2023.zip", 150, "bk23", old=True)
    mk(f"{H}/Desktop/backup-2023 (copy).zip", 150, "bk23", old=True)
    mk(f"{H}/Downloads/project-backup.tar.gz", 210, "pb", old=True)
    mk(f"{H}/Projects/old/project-backup.tar.gz", 210, "pb", old=True)
    mk(f"{H}/Downloads/old-installer.deb", 90, old=True)
    mk(f"{H}/Downloads/google-chrome-stable.deb", 95)
    mk(f"{H}/Downloads/wallpapers.zip", 64)
    for i, n in enumerate(("Neon Rain", "Static Dreams", "Grid Runner", "Black Ice", "Wired", "Lain")):
        mk(f"{H}/Music/Albums/{n}.flac", 28 + 3 * i)
    mk(f"{H}/Music/Singles/Neon Rain.flac", 28, "Albums/Neon Rain.flac")
    mk(f"{H}/Music/Albums/Neon Rain.flac", 28, "Albums/Neon Rain.flac")
    mk(f"{H}/Documents/thesis.pdf", 25)
    for i in range(6):
        mk(f"{H}/Documents/scans/scan_{i}.pdf", 4 + i)
    mk(f"{H}/Projects/spdw-factory/app-release.apk", 42)
    mk(f"{H}/Projects/spdw-factory/build.tar.xz", 38)
    for i in range(5):
        mk(f"{H}/Projects/muos-apps/app{i}.muxapp", 5 + 2 * i)
    mk(f"{H}/.cache/mozilla/firefox/cache2.bin", 240)
    mk(f"{H}/.cache/google-chrome/Default/Cache/data_0", 160)
    mk(f"{H}/.cache/thumbnails/large/thumbs.bin", 70)
    mk(f"{H}/.cache/pip/http/wheelhouse.bin", 85)
    mk(f"{H}/.gradle/caches/modules-2/files.bin", 320)
    mk(f"{H}/.npm/_cacache/content-v2/blob.bin", 140)
    mk(f"{H}/.android/cache/sdk.bin", 45)
    mk(f"{H}/.local/share/Trash/files/old-project.tar", 180)
    mk(f"{H}/.bash_history", 1)
    os.makedirs(f"{H}/Projects/old/empty-dir", exist_ok=True)
    open(f"{H}/Documents/untitled.txt", "w").close()
    try:
        os.symlink(f"{H}/Games/missing-rom.gba", f"{H}/Desktop/dead-link")
    except FileExistsError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--home", default=None, help="demo home to create (default: temp dir)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    home = a.home or os.path.join(tempfile.mkdtemp(prefix="spdw-demo-"), "sirpips")
    build_demo(home)

    from spdw_purge import cats, core, gui
    for m in (core, cats, gui):          # sandbox: all "user" paths point at the demo home
        m.HOME = home
    core.STATE_DIR = os.path.join(home, ".local/state/spdw-purge")
    core.AUDIT_FILE = os.path.join(core.STATE_DIR, "actions.log")

    app = gui.App()
    app.geometry("1180x800+0+0")
    steps = []

    def at(fn, wait=700):
        steps.append((wait, fn))

    def shot(name):
        app.update()
        w, h, x, y = app.winfo_width(), app.winfo_height(), app.winfo_rootx(), app.winfo_rooty()
        subprocess.run(["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", "+repage",
                        os.path.join(a.out, name + ".png")], check=True)
        print("shot", name)

    def open_path(p):
        for iid, path in app.iid2path.items():
            if path == p:
                app.tree.item(iid, open=True)
                app.on_open_iid(iid)
                return iid

    def select_paths(*ps):
        iids = [i for i, p in app.iid2path.items() if p in ps]
        app.tree.selection_set(iids)
        app.on_sel(None)

    def kill_dialogs():
        for w in app.winfo_children():
            if w.winfo_class() == "Toplevel":
                w.destroy()

    # --- SCAN tree
    at(lambda: (app.path_var.set(home), app.do_scan()), 500)
    at(lambda: (open_path(home + "/Games"), open_path(home + "/Games/ROMs"),
                select_paths(home + "/.cache", home + "/Downloads")), 3000)
    at(lambda: shot("scan-tree"), 600)
    # --- MAP
    def to_map():
        app.set_mode("map")
        app.map_sel.update({home + "/Downloads", home + "/.gradle"})
        app.update()
        app.draw_map()
        app.on_sel(None)
    at(to_map, 500)
    at(lambda: shot("scan-map"), 600)
    # --- TYPES
    at(lambda: app.set_mode("types"), 400)
    at(lambda: shot("scan-types"), 600)
    # --- JUNK
    at(lambda: (app.show_tab("junk"), app.junk_scan()), 400)
    at(lambda: (app.junk_select("SAFE"), app.cat_vars["tmp"].set(True)), 5000)
    at(lambda: shot("junk"), 600)
    # --- APPS
    at(lambda: (app.show_tab("apps"), app.big_only.set(True)), 400)
    at(lambda: (app.refilter(), shot("apps")), 4000)
    # --- FINDER
    at(lambda: (app.show_tab("finder"), app.fpath.set(home), app.finder_run()), 400)
    at(lambda: (app.finder_keep_one(), shot("finder-dups")), 3500)
    at(lambda: (app.fmode.set("old_large"), app.finder_mode(), app.fmin.set("100"), app.finder_run()), 400)
    at(lambda: shot("finder-old"), 2500)
    # --- dialog
    def dialog():
        app.show_tab("scan")
        app.set_mode("tree")
        app.refresh_view()
        select_paths(*[p for p in app.iid2path.values() if p in (home + "/.cache", home + "/Downloads")])
        app.after(900, lambda: (shot("dialog"), kill_dialogs()))
        app.do_annihilate()
    at(dialog, 500)

    def run(i=0):
        if i >= len(steps):
            app.after(300, app.destroy)
            return
        wait, fn = steps[i]
        app.after(wait, lambda: (fn(), run(i + 1)))
    app.after(800, run)
    app.mainloop()


if __name__ == "__main__":
    main()
