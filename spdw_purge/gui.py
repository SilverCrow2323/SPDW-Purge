"""SPDW Purge - Tkinter GUI (cyberpunk neon-noir)."""
import os
import queue
import random
import shutil
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import core, finder
from .cats import COVERED, DESTRUCTIVE, build_cats
from .core import (APP, AUTHOR, CRITICAL_PKG, HOME, IS_ROOT, URL, VER, apt_sim_removed, bar, dangerous, destroy,
                   du, fp_cmd, human, load_dpkg, flatpak_list, pkg_sizes, protected, residues_for, run_cmd)

C = dict(bg0="#0d0d1a", bg1="#1a1a2e", panel="#16213e", cyan="#00ffcc", mag="#ff00ff", yel="#f0e130",
         text="#e0e0e0", dim="#6a6a8e", red="#ff2244", orange="#ff6600", grid="#1e3a5f", ink="#070712",
         blue="#3a86ff", violet="#8338ec", green="#06d6a0")
PALETTE = [C["cyan"], C["mag"], C["yel"], C["blue"], C["orange"], C["violet"], C["green"], C["red"]]
TYPE_COLOR = {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(core.TYPE_EXT)}
TYPE_COLOR[core.TYPE_OTHER] = C["dim"]


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.cv = tk.Canvas(self, bg=bg, highlightthickness=0)
        sb = ttk.Scrollbar(self, command=self.cv.yview)
        self.inner = tk.Frame(self.cv, bg=bg)
        self.win = self.cv.create_window(0, 0, anchor="nw", window=self.inner)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfigure(self.win, width=e.width))
        self.cv.bind("<Enter>", self._on)
        self.cv.bind("<Leave>", self._off)
        self.inner.bind("<Enter>", self._on)

    def _on(self, _e):
        self.cv.bind_all("<Button-4>", lambda e: self.cv.yview_scroll(-2, "units"))
        self.cv.bind_all("<Button-5>", lambda e: self.cv.yview_scroll(2, "units"))
        self.cv.bind_all("<MouseWheel>", lambda e: self.cv.yview_scroll(-1 * (e.delta // 120), "units"))

    def _off(self, _e):
        for ev in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.cv.unbind_all(ev)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP}  v{VER}")
        self.geometry("1180x800")
        self.minsize(960, 660)
        self.configure(bg=C["bg0"])
        self.F = self._font()
        self.q = queue.Queue()
        self.busy = False
        self.stop = threading.Event()
        self.scan = None  # ScanResult
        self.sizes, self.iid2path = {}, {}
        self.scan_root, self.mode = "/", "tree"
        self.map_root, self.map_sel, self.map_rects = "/", set(), []
        self.cats = build_cats()
        self.cat_vars, self.cat_lbl, self.cat_size = {}, {}, {}
        self.apps, self.app_by, self.sort = [], {}, ("size", True)
        self.f_paths, self.f_mode = {}, "dups"
        self._style()
        self._header()
        self._tabbar()
        self._console()
        self.content = tk.Frame(self, bg=C["bg0"])
        self.content.pack(fill="both", expand=True, padx=8, pady=4)
        self.pages = {}
        for name, fn in (("scan", self._page_scan), ("junk", self._page_junk),
                         ("apps", self._page_apps), ("finder", self._page_finder)):
            f = tk.Frame(self.content, bg=C["bg0"])
            self.pages[name] = f
            fn(f)
        self.show_tab("scan")
        self.bind("<Control-q>", lambda e: self.destroy())
        self.after(50, self._poll)
        self.update_disk()
        self._glitch()
        mode = "ROOT" if IS_ROOT else "USER (system junk + package removal disabled)"
        self.log(f"{APP} v{VER} online :: mode {mode} :: home {HOME}", "mag")
        self.log("nothing is deleted without confirmation. enable DRY-RUN to rehearse safely.", "dim")

    # ------------------------------------------------------------ plumbing
    def _font(self):
        fams = set(tkfont.families(self))
        for f in ("JetBrains Mono", "Fira Code", "DejaVu Sans Mono", "Liberation Mono", "Courier New"):
            if f in fams:
                return f
        return "Courier"

    def _poll(self):
        self.after(50, self._poll)
        while True:
            try:
                fn = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as ex:  # noqa: BLE001
                self._append(f"[ui] {ex}", "err")

    def ui(self, fn):
        self.q.put(fn)

    def log(self, msg, tag="norm"):
        self.q.put(lambda: self._append(msg, tag))

    def _append(self, msg, tag):
        t = self.con
        t.configure(state="normal")
        t.insert("end", time.strftime("%H:%M:%S ") + msg + "\n", tag)
        if int(t.index("end-1c").split(".")[0]) > 1200:
            t.delete("1.0", "200.0")
        t.see("end")
        t.configure(state="disabled")

    def bg(self, work, then=None, lock=True):
        if lock and self.busy:
            self.log("busy - wait for the current operation to finish.", "warn")
            return
        if lock:
            self.busy = True
            self.busy_lbl.configure(text="▓▒░ WORKING ░▒▓")

        def th():
            res = None
            try:
                res = work()
            except Exception as ex:  # noqa: BLE001
                self.log(f"[!] {ex}", "err")

            def fin():
                if lock:
                    self.busy = False
                    self.busy_lbl.configure(text="")
                if then:
                    then(res)
            self.ui(fin)
        threading.Thread(target=th, daemon=True).start()

    # --------------------------------------------------------------- style
    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("Treeview", background=C["bg0"], fieldbackground=C["bg0"], foreground=C["text"],
                     rowheight=22, borderwidth=0, font=(self.F, 10))
        st.configure("Treeview.Heading", background=C["bg1"], foreground=C["cyan"], relief="flat",
                     font=(self.F, 10, "bold"))
        st.map("Treeview", background=[("selected", "#00473b")], foreground=[("selected", C["cyan"])])
        st.map("Treeview.Heading", background=[("active", C["panel"])])
        st.configure("Vertical.TScrollbar", background=C["bg1"], troughcolor=C["bg0"], bordercolor=C["bg0"],
                     arrowcolor=C["cyan"], lightcolor=C["bg1"], darkcolor=C["bg1"])

    def btn(self, parent, text, cmd, color=None):
        color = color or C["cyan"]
        b = tk.Button(parent, text=text, command=cmd, bg=C["bg1"], fg=color, activebackground=color,
                      activeforeground=C["bg0"], bd=0, relief="flat", highlightthickness=1,
                      highlightbackground=color, highlightcolor=color, font=(self.F, 10, "bold"),
                      padx=10, pady=3, cursor="hand2", disabledforeground=C["dim"])
        b.bind("<Enter>", lambda e: b.cget("state") == "normal" and b.configure(bg=color, fg=C["bg0"]))
        b.bind("<Leave>", lambda e: b.configure(bg=C["bg1"], fg=color))
        return b

    def chk(self, parent, text, var, color=None, cmd=None):
        return tk.Checkbutton(parent, text=text, variable=var, bg=C["bg0"], fg=color or C["text"],
                              selectcolor=C["bg1"], activebackground=C["bg0"], activeforeground=C["cyan"],
                              bd=0, highlightthickness=0, font=(self.F, 10), cursor="hand2", command=cmd)

    def entry(self, parent, var, width):
        return tk.Entry(parent, textvariable=var, width=width, bg=C["ink"], fg=C["cyan"],
                        insertbackground=C["cyan"], bd=0, font=(self.F, 10), highlightthickness=1,
                        highlightbackground=C["grid"], highlightcolor=C["cyan"])

    def tree_widget(self, parent, columns, heads, widths):
        wrap = tk.Frame(parent, bg=C["bg0"], highlightthickness=1, highlightbackground=C["grid"])
        tv = ttk.Treeview(wrap, columns=columns, selectmode="extended")
        for col, (txt, w, anc) in zip(("#0",) + tuple(columns), zip(heads, widths, ["w"] + ["e"] * len(columns))):
            tv.heading(col, text=txt)
            tv.column(col, width=w, anchor=anc, stretch=(col == "#0"))
        sb = ttk.Scrollbar(wrap, command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True)
        tv.tag_configure("dir", foreground=C["cyan"])
        tv.tag_configure("big", foreground=C["orange"])
        tv.tag_configure("dummy", foreground=C["dim"])
        tv.tag_configure("grp", foreground=C["yel"])
        return wrap, tv

    # -------------------------------------------------------------- header
    def _header(self):
        F = self.F
        self.hd = tk.Canvas(self, height=78, bg=C["bg0"], highlightthickness=0)
        self.hd.pack(fill="x")
        for y in range(0, 78, 4):
            self.hd.create_line(0, y, 4000, y, fill="#090914")
        # hex logo
        pts = [(40, 14), (62, 27), (62, 51), (40, 64), (18, 51), (18, 27)]
        self.hd.create_polygon(pts, outline=C["cyan"], fill=C["bg1"], width=2)
        self.hd.create_line(14, 62, 66, 16, fill=C["mag"], width=3)
        self.hd.create_text(40, 39, text="P", font=(F, 22, "bold"), fill=C["cyan"])
        title = "SPDW PURGE"
        self.t_mag = self.hd.create_text(86, 31, anchor="w", text=title, font=(F, 26, "bold"),
                                         fill=C["mag"], state="hidden")
        self.t_red = self.hd.create_text(86, 31, anchor="w", text=title, font=(F, 26, "bold"),
                                         fill=C["red"], state="hidden")
        self.t_main = self.hd.create_text(86, 31, anchor="w", text=title, font=(F, 26, "bold"), fill=C["cyan"])
        self.hd.create_text(88, 60, anchor="w", font=(F, 9), fill=C["dim"],
                            text=f"disk analyzer & cleaner  ::  by {AUTHOR}  ::  v{VER}")
        self.hd.create_line(0, 77, 4000, 77, fill=C["cyan"])
        self.gauge_items = []
        self.hd.bind("<Configure>", lambda e: self.update_disk())
        self.busy_lbl = tk.Label(self, text="", bg=C["bg0"], fg=C["mag"], font=(F, 9, "bold"))
        self.busy_lbl.place(relx=1.0, y=2, anchor="ne")

    def update_disk(self):
        try:
            u = shutil.disk_usage("/")
        except OSError:
            return
        for i in self.gauge_items:
            self.hd.delete(i)
        w = max(self.hd.winfo_width(), 700)
        x1, x0 = w - 20, w - 330
        pct = u.used / u.total
        col = C["cyan"] if pct < 0.7 else C["orange"] if pct < 0.9 else C["red"]
        it = self.gauge_items
        it.append(self.hd.create_text(x0, 24, anchor="w", font=(self.F, 9, "bold"), fill=col,
                                      text=f"/  {human(u.used)} / {human(u.total)}  ::  free {human(u.free)}"))
        it.append(self.hd.create_rectangle(x0, 38, x1, 52, outline=col))
        it.append(self.hd.create_rectangle(x0, 38, x0 + (x1 - x0) * pct, 52, fill=col, outline=col))
        it.append(self.hd.create_text(x1, 64, anchor="e", font=(self.F, 9), fill=col, text=f"{pct * 100:.1f}% USED"))

    def _glitch(self):
        def on():
            dx = random.choice((-4, -3, 3, 4))
            self.hd.coords(self.t_mag, 86 + dx, 31)
            self.hd.coords(self.t_red, 86 - dx, 32)
            self.hd.itemconfigure(self.t_mag, state="normal")
            self.hd.itemconfigure(self.t_red, state="normal")
            self.hd.itemconfigure(self.t_main, fill=random.choice((C["cyan"], C["text"], C["yel"])))
            self.after(random.randint(60, 160), off)

        def off():
            self.hd.itemconfigure(self.t_mag, state="hidden")
            self.hd.itemconfigure(self.t_red, state="hidden")
            self.hd.itemconfigure(self.t_main, fill=C["cyan"])
            self.after(random.randint(2500, 7000), on)
        self.after(3000, on)

    # ------------------------------------------------------------- tab bar
    def _tabbar(self):
        bar_ = tk.Frame(self, bg=C["bg0"])
        bar_.pack(fill="x", padx=8, pady=(8, 0))
        self.tab_btns = {}
        for key, label in (("scan", "[ 01 :: SCAN ]"), ("junk", "[ 02 :: JUNK ]"),
                           ("apps", "[ 03 :: APPS ]"), ("finder", "[ 04 :: FINDER ]")):
            b = tk.Button(bar_, text=label, command=lambda k=key: self.show_tab(k), bd=0, relief="flat",
                          font=(self.F, 11, "bold"), padx=14, pady=4, cursor="hand2", highlightthickness=0)
            b.pack(side="left", padx=(0, 4))
            self.tab_btns[key] = b
        self.btn(bar_, "?", self.about, C["dim"]).pack(side="right", padx=(8, 0))
        tk.Label(bar_, text=f"MODE:{'ROOT' if IS_ROOT else 'USER'}", bg=C["bg0"],
                 fg=C["mag"] if IS_ROOT else C["orange"], font=(self.F, 10, "bold")).pack(side="right", padx=8)
        self.dry_var = tk.BooleanVar(value=core.DRY.is_set())
        self.chk(bar_, "DRY-RUN (rehearsal, changes nothing)", self.dry_var, C["yel"], self.toggle_dry).pack(side="right")

    def toggle_dry(self):
        if self.dry_var.get():
            core.DRY.set()
            self.log("DRY-RUN ON: every delete/purge is only simulated and logged.", "warn")
        else:
            core.DRY.clear()
            self.log("DRY-RUN OFF: operations are REAL again.", "err")

    def show_tab(self, name):
        for f in self.pages.values():
            f.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for k, b in self.tab_btns.items():
            on = k == name
            b.configure(bg=C["cyan"] if on else C["bg1"], fg=C["bg0"] if on else C["cyan"],
                        activebackground=C["cyan"], activeforeground=C["bg0"])
        if name == "apps" and not self.apps:
            self.load_apps()

    def _console(self):
        box = tk.Frame(self, bg=C["bg0"], highlightthickness=1, highlightbackground=C["grid"])
        box.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        head = tk.Frame(box, bg=C["bg0"])
        head.pack(fill="x", padx=6)
        tk.Label(head, text="[ CONSOLE ]", bg=C["bg0"], fg=C["cyan"], font=(self.F, 9, "bold")).pack(side="left")
        tk.Label(head, text="audit log: " + core.AUDIT_FILE.replace(HOME, "~"), bg=C["bg0"], fg=C["dim"],
                 font=(self.F, 8)).pack(side="right")
        inner = tk.Frame(box, bg=C["ink"])
        inner.pack(fill="x", padx=4, pady=(0, 4))
        self.con = tk.Text(inner, height=7, bg=C["ink"], fg="#00cc99", bd=0, font=(self.F, 9), wrap="word",
                           state="disabled", highlightthickness=0)
        sb = ttk.Scrollbar(inner, command=self.con.yview)
        self.con.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.con.pack(side="left", fill="x", expand=True)
        for t, c in (("norm", "#00cc99"), ("ok", C["cyan"]), ("warn", C["orange"]), ("err", C["red"]),
                     ("dim", C["dim"]), ("mag", C["mag"])):
            self.con.tag_configure(t, foreground=c)

    # ------------------------------------------------------------- dialogs
    def ask(self, title, lines, level="warn", typed=None, yes="EXECUTE", abort=True):
        col = {"warn": C["orange"], "crit": C["red"], "info": C["cyan"]}[level]
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.configure(bg=col)
        dlg.transient(self)
        dlg.resizable(False, False)
        inner = tk.Frame(dlg, bg=C["bg0"])
        inner.pack(padx=2, pady=2)
        tk.Label(inner, text=f"╔═ {title} ═╗", bg=C["bg0"], fg=col, font=(self.F, 12, "bold")).pack(padx=16, pady=(12, 6))
        txt = tk.Text(inner, width=84, height=min(max(len(lines), 3), 16), bg=C["ink"], fg=C["text"], bd=0,
                      font=(self.F, 9), wrap="none", highlightthickness=0)
        txt.pack(padx=16, pady=4)
        for ln in lines:
            txt.insert("end", ln + "\n")
        txt.configure(state="disabled")
        res = {"ok": False}
        var = tk.StringVar()
        ent = None
        if typed:
            tk.Label(inner, text=f"type  {typed}  to proceed:", bg=C["bg0"], fg=col, font=(self.F, 10)).pack()
            ent = tk.Entry(inner, textvariable=var, bg=C["ink"], fg=col, insertbackground=col, bd=0,
                           font=(self.F, 11), justify="center", highlightthickness=1, highlightbackground=col)
            ent.pack(pady=4)
        row = tk.Frame(inner, bg=C["bg0"])
        row.pack(pady=12)
        if abort:
            self.btn(row, "[ ABORT ]", dlg.destroy, C["dim"]).pack(side="left", padx=6)
        go = self.btn(row, f"[ {yes} ]", lambda: (res.update(ok=True), dlg.destroy()), col)
        go.pack(side="left", padx=6)
        if typed:
            go.configure(state="disabled")
            var.trace_add("write", lambda *a: go.configure(state="normal" if var.get() == typed else "disabled"))
            ent.focus_set()
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.wait_visibility()
        dlg.grab_set()
        self.wait_window(dlg)
        return res["ok"]

    def about(self):
        self.ask(f"{APP} v{VER}", [
            f"  {APP} - disk analyzer & cleaner for Debian / MX Linux",
            f"  made by {AUTHOR}",
            f"  {URL}",
            "  license: MIT", "",
            "  SCAN    treemap, tree, top files, file types",
            "  JUNK    system + user junk, old kernels, snapshots",
            "  APPS    full package removal + leftover hunting",
            "  FINDER  duplicates, empty files, broken links, big & old", "",
            "  every action is confirmed and written to the audit log.",
        ], "info", yes="CLOSE", abort=False)

    def pick(self, title, items, on_ok):
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.configure(bg=C["mag"])
        dlg.transient(self)
        inner = tk.Frame(dlg, bg=C["bg0"])
        inner.pack(padx=2, pady=2)
        tk.Label(inner, text=f"╔═ {title} ═╗", bg=C["bg0"], fg=C["mag"], font=(self.F, 12, "bold")).pack(padx=16, pady=(12, 6))
        vars_ = []
        for p, sz in items[:25]:
            v = tk.BooleanVar(value=True)
            vars_.append((v, p))
            self.chk(inner, f"{human(sz):>8}  {p}", v, C["text"]).pack(anchor="w", padx=16)
        row = tk.Frame(inner, bg=C["bg0"])
        row.pack(pady=12)

        def go():
            sel = [p for v, p in vars_ if v.get()]
            dlg.destroy()
            on_ok(sel)
        self.btn(row, "[ SKIP ]", dlg.destroy, C["dim"]).pack(side="left", padx=6)
        self.btn(row, "[ WIPE SELECTED ]", go, C["mag"]).pack(side="left", padx=6)
        dlg.update_idletasks()
        dlg.geometry(f"+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 80}")
        dlg.wait_visibility()
        dlg.grab_set()

    def delete_paths(self, paths, shred, done):
        """Shared confirm + delete flow. done(freed, paths) runs on the UI thread."""
        bad = [p for p in paths if protected(p)]
        if bad:
            self.log("PROTECTED, untouchable: " + ", ".join(bad), "err")
            paths = [p for p in paths if p not in bad]
        if not paths:
            return
        total = sum(du(p) for p in paths)
        danger = any(dangerous(p) for p in paths)
        dry = core.DRY.is_set()
        lines = [f"{len(paths)} items :: {human(total)} :: " + ("DRY-RUN (simulated)" if dry else "PERMANENT deletion")
                 + (" + SHRED" if shred else ""), ""]
        lines += [("[!] " if dangerous(p) else "    ") + p for p in paths[:40]]
        if len(paths) > 40:
            lines.append(f"    ... and {len(paths) - 40} more")
        if danger:
            lines += ["", "[!] SYSTEM AREA: deleting here can BREAK the installation."]
        if not self.ask("ANNIHILATE", lines, "crit" if danger else "warn",
                        typed="ANNIHILATE" if danger and not dry else None):
            self.log("operation cancelled.", "dim")
            return
        sizes = {p: du(p) for p in paths}

        def work():
            return sum(destroy(p, shred, self.log) for p in paths)

        def then(freed):
            self.log(f"== {'would free' if dry else 'freed'} ~{human(freed or 0)} ==", "mag")
            self.update_disk()
            done(freed, paths, sizes)
        self.bg(work, then)

    # ================================================================ SCAN
    def _page_scan(self, f):
        top = tk.Frame(f, bg=C["bg0"])
        top.pack(fill="x", pady=(6, 4))
        tk.Label(top, text="TARGET>", bg=C["bg0"], fg=C["cyan"], font=(self.F, 10, "bold")).pack(side="left")
        self.path_var = tk.StringVar(value="/")
        self.entry(top, self.path_var, 30).pack(side="left", padx=6, ipady=3)
        self.btn(top, "/", lambda: self.path_var.set("/")).pack(side="left", padx=2)
        self.btn(top, "~", lambda: self.path_var.set(HOME)).pack(side="left", padx=2)
        self.btn(top, "[ SCAN ]", self.do_scan, C["cyan"]).pack(side="left", padx=(10, 2))
        self.btn(top, "[ STOP ]", self.stop.set, C["orange"]).pack(side="left", padx=2)
        self.mode_btns = {}
        for key, label in (("tree", "TREE"), ("top", "TOP 300"), ("map", "MAP"), ("types", "TYPES")):
            b = self.btn(top, f"[ {label} ]", lambda k=key: self.set_mode(k), C["yel"])
            b.pack(side="left", padx=(10 if key == "tree" else 2, 2))
            self.mode_btns[key] = b
        self.btn(top, "[ UP ]", self.map_up, C["blue"]).pack(side="left", padx=(10, 2))
        self.scan_lbl = tk.Label(f, text="ready. pick a target and hit SCAN.", bg=C["bg0"], fg=C["dim"],
                                 font=(self.F, 9), anchor="w")
        self.scan_lbl.pack(fill="x")
        holder = tk.Frame(f, bg=C["bg0"])
        holder.pack(fill="both", expand=True)
        self.view_tree, self.tree = self.tree_widget(
            holder, ("size", "pct"), ("PATH", "SIZE", "% OF PARENT"), (560, 100, 170))
        self.view_cv = tk.Frame(holder, bg=C["bg0"], highlightthickness=1, highlightbackground=C["grid"])
        self.cv = tk.Canvas(self.view_cv, bg=C["bg0"], highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.after_idle(self.draw_canvas))
        self.cv.bind("<Button-1>", self.map_click)
        self.cv.bind("<Double-Button-1>", self.map_dbl)
        self.cv.bind("<Motion>", self.map_hover)
        self.view_tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewOpen>>", lambda e: self.on_open_iid(self.tree.focus()))
        self.tree.bind("<<TreeviewSelect>>", self.on_sel)
        self.tree.bind("<Button-3>", self.ctx_menu)
        bot = tk.Frame(f, bg=C["bg0"])
        bot.pack(fill="x", pady=6)
        self.shred_var = tk.BooleanVar(value=False)
        self.chk(bot, "SHRED (overwrite before delete - slow, useless on SSD/btrfs)", self.shred_var,
                 C["orange"]).pack(side="left")
        self.btn(bot, "[ ANNIHILATE SELECTION ]", self.do_annihilate, C["red"]).pack(side="right")
        self.btn(bot, "[ EXPORT REPORT ]", self.export_report, C["blue"]).pack(side="right", padx=8)
        self.sel_lbl = tk.Label(bot, text="", bg=C["bg0"], fg=C["mag"], font=(self.F, 10, "bold"))
        self.sel_lbl.pack(side="right", padx=14)
        self.ctx = tk.Menu(self, tearoff=0, bg=C["bg1"], fg=C["cyan"], activebackground=C["cyan"],
                           activeforeground=C["bg0"], bd=0, font=(self.F, 10))

    def do_scan(self):
        root = os.path.expanduser(self.path_var.get().strip() or "/")
        if not os.path.isdir(root):
            self.log(f"invalid path: {root}", "err")
            return
        self.stop.clear()
        self.scan_root = self.map_root = os.path.normpath(root)
        self.map_sel.clear()
        self.log(f"scanning {self.scan_root} ...", "mag")
        t0 = time.time()

        def cb(d, n, nd):
            self.ui(lambda: self.scan_lbl.configure(text=f"▓ {nd} folders / {n} files :: {d[-90:]}", fg=C["mag"]))

        def then(res):
            if res is None:
                self.scan_lbl.configure(text="scan interrupted.", fg=C["orange"])
                self.log("scan interrupted.", "warn")
                return
            self.scan, self.sizes = res, res.sizes
            tot = self.sizes.get(self.scan_root, 0)
            self.scan_summary = f"{res.nfiles} files :: {human(tot)} in {self.scan_root} :: {time.time() - t0:.0f}s"
            self.scan_lbl.configure(text=self.scan_summary, fg=C["cyan"])
            self.log(f"scan ok: {human(tot)} in {res.nfiles} files ({time.time() - t0:.0f}s)", "ok")
            self.refresh_view()
        self.bg(lambda: core.scan_tree(self.scan_root, cb, self.stop), then)

    def set_mode(self, m):
        self.mode = m
        for k, b in self.mode_btns.items():
            b.configure(relief="solid" if k == m else "flat")
        if self.scan:
            self.refresh_view()

    def refresh_view(self):
        if self.mode in ("tree", "top"):
            self.scan_lbl.configure(text=getattr(self, "scan_summary", ""), fg=C["cyan"])
            self.view_cv.pack_forget()
            self.view_tree.pack(fill="both", expand=True)
            self.fill_tree()
        else:
            self.view_tree.pack_forget()
            self.view_cv.pack(fill="both", expand=True)
            self.after_idle(self.draw_canvas)

    # ---- tree / top
    def fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.iid2path.clear()
        if self.mode == "top":
            for sz, p in self.scan.top:
                iid = self.tree.insert("", "end", text=p, values=(human(sz), ""), tags=("big" if sz > 1 << 30 else "",))
                self.iid2path[iid] = p
            return
        tot = self.sizes.get(self.scan_root, 0)
        iid = self.tree.insert("", "end", text=self.scan_root, values=(human(tot), bar(1.0)), tags=("dir",))
        self.iid2path[iid] = self.scan_root
        self.tree.insert(iid, "end", text="…", tags=("dummy",))
        self.tree.item(iid, open=True)
        self.on_open_iid(iid)

    def on_open_iid(self, iid):
        kids = self.tree.get_children(iid)
        if len(kids) != 1 or "dummy" not in self.tree.item(kids[0], "tags"):
            return
        self.tree.delete(kids[0])
        path = self.iid2path.get(iid)
        if not path:
            return
        rows = core.children_sizes(path, self.sizes)
        parent = self.sizes.get(path) or sum(r[0] for r in rows) or 1
        for sz, p, name, isdir in rows[:400]:
            tags = ("dir",) if isdir else (("big",) if sz > 1 << 30 else ())
            ch = self.tree.insert(iid, "end", text=name, values=(human(sz), bar(sz / parent)), tags=tags)
            self.iid2path[ch] = p
            if isdir and self.sizes.get(p, 0) > 0:
                self.tree.insert(ch, "end", text="…", tags=("dummy",))
        if len(rows) > 400:
            self.tree.insert(iid, "end", text=f"... {len(rows) - 400} smaller items hidden", tags=("dummy",))

    # ---- treemap / types
    def draw_canvas(self):
        if self.mode == "map":
            self.draw_map()
        elif self.mode == "types":
            self.draw_types()

    def draw_map(self):
        cv = self.cv
        cv.delete("all")
        self.map_rects = []
        W, H = cv.winfo_width(), cv.winfo_height()
        if not self.scan or W < 60:
            return
        rows = [r for r in core.children_sizes(self.map_root, self.sizes) if r[0] > 0]
        items = [(sz, (p, name, isdir, sz)) for sz, p, name, isdir in rows[:60]]
        if len(rows) > 60:
            rest = sum(r[0] for r in rows[60:])
            items.append((rest, (None, f"… {len(rows) - 60} more", False, rest)))
        total = sum(v for v, _ in items) or 1
        for i, (x, y, w, h, (p, name, isdir, sz)) in enumerate(core.squarify(items, 3, 3, W - 6, H - 6)):
            col = PALETTE[i % len(PALETTE)] if p else C["dim"]
            sel = p in self.map_sel
            cv.create_rectangle(x, y, x + w, y + h, fill=mix(col, C["bg0"], 0.80 if isdir else 0.90),
                                outline=C["mag"] if sel else mix(col, C["bg0"], 0.35), width=3 if sel else 1)
            if w > 64 and h > 34:
                cv.create_text(x + 7, y + 5, anchor="nw", fill=col, font=(self.F, 9, "bold"),
                               text=(("▸ " if isdir else "") + name)[:max(int(w / 7) - 1, 3)])
                cv.create_text(x + 7, y + 20, anchor="nw", fill=C["dim"], font=(self.F, 9),
                               text=f"{human(sz)}  {sz / total * 100:.0f}%")
            self.map_rects.append((x, y, x + w, y + h, p, isdir, sz, name))
        self.scan_lbl.configure(text=f"MAP :: {self.map_root} :: {human(self.sizes.get(self.map_root, 0))}"
                                     "  (click = select, double-click = dive in, UP = back)", fg=C["cyan"])

    def _rect_at(self, e):
        for r in self.map_rects:
            if r[0] <= e.x <= r[2] and r[1] <= e.y <= r[3]:
                return r
        return None

    def map_hover(self, e):
        r = self._rect_at(e)
        if r:
            self.scan_lbl.configure(text=f"{r[4] or r[7]}  ::  {human(r[6])}", fg=C["yel"])

    def map_click(self, e):
        r = self._rect_at(e)
        if r and r[4]:
            self.map_sel.symmetric_difference_update({r[4]})
            self.draw_map()
            self.on_sel(None)

    def map_dbl(self, e):
        r = self._rect_at(e)
        if r and r[4] and r[5]:
            self.map_root = r[4]
            self.map_sel.clear()
            self.draw_map()

    def map_up(self):
        if self.mode == "map" and self.scan and self.map_root != self.scan_root:
            self.map_root = os.path.dirname(self.map_root)
            self.map_sel.clear()
            self.draw_map()

    def draw_types(self):
        cv = self.cv
        cv.delete("all")
        W = cv.winfo_width()
        if not self.scan or W < 200:
            return
        types = sorted(self.scan.types.items(), key=lambda kv: -kv[1])
        total = sum(v for _, v in types) or 1
        cv.create_text(16, 14, anchor="nw", fill=C["cyan"], font=(self.F, 11, "bold"),
                       text=f"FILE TYPES in {self.scan_root}  ::  {human(total)} of regular files")
        y, bw = 52, W - 360
        for name, sz in types:
            col = TYPE_COLOR.get(name, C["dim"])
            cv.create_text(16, y + 8, anchor="w", fill=col, font=(self.F, 10, "bold"), text=name.upper())
            cv.create_rectangle(190, y, 190 + bw, y + 18, outline=C["grid"])
            cv.create_rectangle(190, y, 190 + max(bw * sz / types[0][1], 2), y + 18, fill=col, outline=col)
            cv.create_text(200 + bw, y + 8, anchor="w", fill=C["text"], font=(self.F, 10),
                           text=f"{human(sz):>8}  {sz / total * 100:5.1f}%")
            y += 32
        self.scan_lbl.configure(text="TYPES :: bytes by extension bucket (Other = unknown extensions)", fg=C["cyan"])

    # ---- selection / actions
    def selected_paths(self):
        if self.mode == "map":
            paths = sorted(self.map_sel, key=len)
        else:
            paths = sorted({self.iid2path[i] for i in self.tree.selection() if i in self.iid2path}, key=len)
        out = []
        for p in paths:
            if not any(p.startswith(o + "/") for o in out):
                out.append(p)
        return out

    def on_sel(self, _e):
        ps = self.selected_paths()
        if not ps:
            self.sel_lbl.configure(text="")
        elif len(ps) <= 8:
            self.sel_lbl.configure(text=f"{len(ps)} selected :: {human(sum(self.sizes.get(p) or du(p) for p in ps))}")
        else:
            self.sel_lbl.configure(text=f"{len(ps)} selected")

    def forget(self, path, size):
        """Keep the scan data consistent after deleting `path` (no rescan needed)."""
        for k in [k for k in self.sizes if k == path or k.startswith(path + "/")]:
            del self.sizes[k]
        a = os.path.dirname(path)
        while a and len(a) >= len(self.scan_root):
            if a in self.sizes:
                self.sizes[a] = max(self.sizes[a] - size, 0)
            if a == self.scan_root:
                break
            a = os.path.dirname(a)

    def do_annihilate(self):
        ps = self.selected_paths()
        if not ps:
            self.log("nothing selected.", "warn")
            return

        def done(freed, paths, sizes):
            if core.DRY.is_set():
                return
            for p in paths:
                if not os.path.lexists(p):
                    self.forget(p, sizes.get(p, 0))
            self.map_sel.clear()
            if self.scan:
                self.scan.top[:] = [(s, p) for s, p in self.scan.top if os.path.lexists(p)]
                self.refresh_view()
            self.on_sel(None)
        self.delete_paths(ps, self.shred_var.get(), done)

    def ctx_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid or iid not in self.iid2path:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        p = self.iid2path[iid]
        self.ctx.delete(0, "end")
        self.ctx.add_command(label="Open in file manager", command=lambda: core.open_in_file_manager(p))
        self.ctx.add_command(label="Copy path", command=lambda: (self.clipboard_clear(), self.clipboard_append(p)))
        if os.path.isdir(p):
            self.ctx.add_command(label="Scan this folder", command=lambda: (self.path_var.set(p), self.do_scan()))
        self.ctx.add_separator()
        self.ctx.add_command(label="Annihilate selection...", command=self.do_annihilate)
        self.ctx.tk_popup(e.x_root, e.y_root)

    def export_report(self):
        if not self.scan:
            self.log("run a scan first.", "warn")
            return
        path = os.path.join(HOME, time.strftime("spdw-purge-report-%Y%m%d-%H%M.md"))
        junk = {c.title: self.cat_size[c.key] for c in self.cats if self.cat_size.get(c.key)}
        core.write_report(path, self.scan, self.scan_root, junk)
        self.log(f"report written: {path}", "ok")

    # ================================================================ JUNK
    def _page_junk(self, f):
        top = tk.Frame(f, bg=C["bg0"])
        top.pack(fill="x", pady=(6, 6))
        self.btn(top, "[ SCAN JUNK ]", self.junk_scan, C["cyan"]).pack(side="left")
        self.btn(top, "[ SELECT SAFE ]", lambda: self.junk_select("SAFE"), C["yel"]).pack(side="left", padx=6)
        self.btn(top, "[ ALL ]", lambda: self.junk_select("ALL"), C["orange"]).pack(side="left")
        self.btn(top, "[ NONE ]", lambda: self.junk_select(None), C["dim"]).pack(side="left", padx=6)
        self.btn(top, "[ PURGE SELECTED ]", self.junk_purge, C["red"]).pack(side="right")
        self.junk_tot = tk.Label(top, text="", bg=C["bg0"], fg=C["mag"], font=(self.F, 11, "bold"))
        self.junk_tot.pack(side="right", padx=14)
        sf = ScrollFrame(f, C["bg0"])
        sf.pack(fill="both", expand=True)
        grid = sf.inner
        colr = {"SAFE": C["cyan"], "MED": C["orange"], "AGGR": C["red"]}
        for i, c in enumerate(self.cats):
            v = tk.BooleanVar(value=False)
            v.trace_add("write", lambda *a: self.junk_recalc())
            self.cat_vars[c.key] = v
            ok = c.usable()
            col = colr[c.level] if ok else C["dim"]
            tk.Checkbutton(grid, text=c.title, variable=v, bg=C["bg0"], fg=col, selectcolor=C["bg1"],
                           activebackground=C["bg0"], activeforeground=C["cyan"], bd=0, highlightthickness=0,
                           font=(self.F, 10, "bold"), anchor="w", width=19, cursor="hand2",
                           state="normal" if ok else "disabled").grid(row=i, column=0, sticky="w", padx=8, pady=1)
            tk.Label(grid, text=f"[{c.level}]" + ("[ROOT]" if c.root else ""), bg=C["bg0"], fg=col,
                     font=(self.F, 9)).grid(row=i, column=1, padx=6, sticky="w")
            tk.Label(grid, text=c.desc, bg=C["bg0"], fg=C["dim"], font=(self.F, 9), anchor="w").grid(
                row=i, column=2, sticky="w", padx=6)
            lbl = tk.Label(grid, text="--" if ok else "n/a", bg=C["bg0"], fg=C["text"], font=(self.F, 10, "bold"),
                           width=24, anchor="e")
            lbl.grid(row=i, column=3, sticky="e", padx=10)
            self.cat_lbl[c.key] = lbl
        grid.grid_columnconfigure(2, weight=1)

    def junk_scan(self):
        self.log("scanning junk ...", "mag")
        cats = [c for c in self.cats if c.usable()]
        for c in cats:
            self.cat_lbl[c.key].configure(text="...", fg=C["mag"])

        def work():
            for c in cats:
                try:
                    sz, note = c.scan()
                except Exception as ex:  # noqa: BLE001
                    sz, note = 0, f"err {ex}"
                self.cat_size[c.key] = sz or 0
                txt = (human(sz) if sz else "0B") + (f"  {note}" if note else "")
                self.ui(lambda k=c.key, t=txt, z=sz: self.cat_lbl[k].configure(text=t, fg=C["cyan"] if z else C["dim"]))
        self.bg(work, lambda _r: (self.junk_recalc(), self.log("junk scan complete.", "ok")))

    def junk_select(self, mode):
        for c in self.cats:
            if c.usable():
                self.cat_vars[c.key].set(mode == "ALL" or (mode == "SAFE" and c.level == "SAFE"))

    def junk_chosen(self):
        return [c for c in self.cats if c.usable() and self.cat_vars[c.key].get()]

    def junk_recalc(self):
        ch = {c.key for c in self.junk_chosen()}
        skip = set()
        for k, cov in COVERED.items():
            if k in ch:
                skip |= cov
        tot = sum(self.cat_size.get(k, 0) for k in ch if k not in skip)
        self.junk_tot.configure(text=f"selected: ~{human(tot)}" if ch else "")

    def junk_purge(self):
        ch = self.junk_chosen()
        if not ch:
            self.log("no category selected.", "warn")
            return
        hard = any(c.level == "AGGR" or c.key in DESTRUCTIVE for c in ch)
        dry = core.DRY.is_set()
        lines = [f"{c.title:<20} [{c.level}] {human(self.cat_size.get(c.key, 0))}" for c in ch]
        lines += ["", "DRY-RUN: nothing will be changed." if dry else "Deletion is permanent."]
        if not self.ask("PURGE JUNK", lines, "crit" if hard else "warn", typed="PURGE" if hard and not dry else None):
            self.log("operation cancelled.", "dim")
            return

        def work():
            skip = set()
            for k, cov in COVERED.items():
                if k in {c.key for c in ch}:
                    skip |= cov
            for c in ch:
                if c.key in skip:
                    continue
                self.log(f">> {c.title}", "mag")
                try:
                    c.run(self.log)
                except Exception as ex:  # noqa: BLE001
                    self.log(f"[!] {c.title}: {ex}", "err")
        self.bg(work, lambda _r: (self.update_disk(), self.log("== purge complete ==", "mag"),
                                  self.after(300, self.junk_scan)))

    # ================================================================ APPS
    def _page_apps(self, f):
        top = tk.Frame(f, bg=C["bg0"])
        top.pack(fill="x", pady=(6, 4))
        tk.Label(top, text="FILTER>", bg=C["bg0"], fg=C["cyan"], font=(self.F, 10, "bold")).pack(side="left")
        self.flt = tk.StringVar()
        self.flt.trace_add("write", lambda *a: self.refilter())
        self.entry(top, self.flt, 26).pack(side="left", padx=6, ipady=3)
        self.big_only = tk.BooleanVar(value=False)
        self.chk(top, "> 10 MB only", self.big_only, C["text"], self.refilter).pack(side="left", padx=4)
        self.btn(top, "[ RELOAD ]", self.load_apps, C["cyan"]).pack(side="left", padx=4)
        self.cnt_lbl = tk.Label(top, text="", bg=C["bg0"], fg=C["dim"], font=(self.F, 9))
        self.cnt_lbl.pack(side="left", padx=8)
        self.btn(top, "[ VAPORIZE SELECTED ]", self.apps_purge, C["red"]).pack(side="right")
        wrap, self.atree = self.tree_widget(f, ("size", "ver", "desc"), ("PACKAGE", "SIZE", "VERSION", "DESCRIPTION"),
                                            (270, 90, 150, 420))
        wrap.pack(fill="both", expand=True)
        self.atree.column("desc", anchor="w")
        self.atree.column("ver", anchor="w")
        for col, key in (("#0", "name"), ("size", "size"), ("ver", "ver"), ("desc", "desc")):
            self.atree.heading(col, command=lambda k=key: self.sort_apps(k))
        self.atree.tag_configure("prot", foreground=C["dim"])
        self.atree.tag_configure("fp", foreground=C["mag"])
        bot = tk.Frame(f, bg=C["bg0"])
        bot.pack(fill="x", pady=6)
        self.autorm = tk.BooleanVar(value=True)
        self.resid = tk.BooleanVar(value=True)
        self.chk(bot, "autoremove --purge afterwards", self.autorm).pack(side="left")
        self.chk(bot, "hunt leftovers (~/.config ~/.cache ...)", self.resid).pack(side="left", padx=12)
        tk.Label(bot, text="grey = essential (locked)   magenta = flatpak", bg=C["bg0"], fg=C["dim"],
                 font=(self.F, 9)).pack(side="right")

    def load_apps(self):
        self.log("loading package list ...", "mag")

        def then(res):
            self.apps = res or []
            self.app_by = {a["iid"]: a for a in self.apps}
            self.refilter()
            self.log(f"{len(self.apps)} packages / apps loaded.", "ok")
        self.bg(lambda: load_dpkg() + flatpak_list(), then)

    def sort_apps(self, key):
        self.sort = (key, not self.sort[1] if self.sort[0] == key else key != "name")
        self.refilter()

    def refilter(self):
        q = self.flt.get().strip().lower()
        key, rev = self.sort
        big = self.big_only.get()
        rows = [a for a in self.apps if (not q or q in a["name"].lower() or q in a["desc"].lower())
                and (not big or a["size"] > 10 << 20)]
        rows.sort(key=lambda a: (a[key].lower() if isinstance(a[key], str) else a[key]), reverse=rev)
        self.atree.delete(*self.atree.get_children())
        for a in rows[:4000]:
            tags = ("prot",) if a["prot"] else (("fp",) if a["kind"] == "fp" else ())
            self.atree.insert("", "end", iid=a["iid"], text=a["name"],
                              values=(human(a["size"]), a["ver"], a["desc"]), tags=tags)
        self.cnt_lbl.configure(text=f"{min(len(rows), 4000)}/{len(self.apps)} :: total {human(sum(a['size'] for a in self.apps))}")

    def apps_purge(self):
        if not IS_ROOT and not core.DRY.is_set():
            self.log("ROOT required to remove packages: start with the normal launcher (pkexec).", "err")
            return
        sel = [self.app_by[i] for i in self.atree.selection() if i in self.app_by]
        locked = [a["name"] for a in sel if a["prot"]]
        if locked:
            self.log("essential, ignored: " + ", ".join(locked), "warn")
        sel = [a for a in sel if not a["prot"]]
        if not sel:
            self.log("nothing selected.", "warn")
            return
        debs = [a["name"] for a in sel if a["kind"] == "deb"]
        fps = [a for a in sel if a["kind"] == "fp"]
        self.log("simulating removal ...", "mag")

        def then(removed):
            extra = [r for r in removed if r not in debs and r.split(":")[0] not in debs]
            crit = [r for r in removed if CRITICAL_PKG.match(r)]
            if debs and not removed:
                self.log("apt cannot simulate the removal (unknown package or lock held).", "err")
                return
            size = pkg_sizes(removed) + sum(a["size"] for a in fps)
            lines = [f"{len(debs)} packages + {len(fps)} flatpaks selected :: ~{human(size)} to free", ""]
            lines += [f"    {n}" for n in debs + [a['name'] for a in fps]][:25]
            if extra:
                lines += ["", f"DEPENDENCIES DRAGGED ALONG ({len(extra)}):"] + [f"  + {n}" for n in extra[:25]]
            if crit:
                lines += ["", "[!] CRITICAL COMPONENTS IN THE LIST: " + ", ".join(crit[:8])]
            hard = (crit or len(extra) > 15) and not core.DRY.is_set()
            if not self.ask("VAPORIZE", lines, "crit" if hard else "warn", typed="VAPORIZE" if hard else None):
                self.log("operation cancelled.", "dim")
                return
            self.apps_exec(debs, fps)
        self.bg(lambda: apt_sim_removed(["purge"] + debs) if debs else [], then)

    def apps_exec(self, debs, fps):
        autorm, resid = self.autorm.get(), self.resid.get()

        def work():
            if debs:
                run_cmd(["apt-get", "purge", "-y"] + debs, self.log)
                if autorm:
                    run_cmd(["apt-get", "autoremove", "--purge", "-y"], self.log)
            for a in fps:
                run_cmd(fp_cmd(a["scope"], ["uninstall", "-y", "--delete-data", a["app"]]), self.log)
            if fps:
                run_cmd(["flatpak", "uninstall", "--unused", "-y", "--system"], self.log)
            return residues_for(debs) if (resid and debs and not core.DRY.is_set()) else []

        def then(found):
            self.update_disk()
            self.log("== removal complete ==", "mag")
            self.apps = []
            self.load_apps()
            if found:
                self.pick("LEFTOVERS FOUND", found, self.kill_residues)
        self.bg(work, then)

    def kill_residues(self, paths):
        if not paths:
            return
        self.bg(lambda: sum(destroy(p, False, self.log) for p in paths if not protected(p)),
                lambda f: (self.update_disk(), self.log(f"leftovers wiped: ~{human(f or 0)}", "mag")))

    # ============================================================== FINDER
    def _page_finder(self, f):
        top = tk.Frame(f, bg=C["bg0"])
        top.pack(fill="x", pady=(6, 4))
        tk.Label(top, text="TARGET>", bg=C["bg0"], fg=C["cyan"], font=(self.F, 10, "bold")).pack(side="left")
        self.fpath = tk.StringVar(value=HOME)
        self.entry(top, self.fpath, 30).pack(side="left", padx=6, ipady=3)
        self.btn(top, "/", lambda: self.fpath.set("/")).pack(side="left", padx=2)
        self.btn(top, "~", lambda: self.fpath.set(HOME)).pack(side="left", padx=2)
        self.btn(top, "[ RUN ]", self.finder_run, C["cyan"]).pack(side="left", padx=(10, 2))
        self.btn(top, "[ STOP ]", self.stop.set, C["orange"]).pack(side="left", padx=2)
        row2 = tk.Frame(f, bg=C["bg0"])
        row2.pack(fill="x", pady=(0, 4))
        self.fmode = tk.StringVar(value="dups")
        for key, label in (("dups", "DUPLICATES"), ("empty_files", "EMPTY FILES"), ("empty_dirs", "EMPTY DIRS"),
                           ("broken_links", "BROKEN LINKS"), ("old_large", "BIG & OLD")):
            tk.Radiobutton(row2, text=label, variable=self.fmode, value=key, indicatoron=0, bg=C["bg1"],
                           fg=C["yel"], selectcolor=C["panel"], activebackground=C["panel"],
                           activeforeground=C["yel"], bd=0, highlightthickness=1, highlightbackground=C["yel"],
                           font=(self.F, 10, "bold"), padx=10, pady=2, cursor="hand2",
                           command=self.finder_mode).pack(side="left", padx=2)
        tk.Label(row2, text="min size MB>", bg=C["bg0"], fg=C["dim"], font=(self.F, 9)).pack(side="left", padx=(14, 2))
        self.fmin = tk.StringVar(value="1")
        self.entry(row2, self.fmin, 6).pack(side="left", ipady=2)
        tk.Label(row2, text="older than days>", bg=C["bg0"], fg=C["dim"], font=(self.F, 9)).pack(side="left", padx=(10, 2))
        self.fdays = tk.StringVar(value="180")
        self.entry(row2, self.fdays, 5).pack(side="left", ipady=2)
        self.f_lbl = tk.Label(f, text="duplicates are matched by size, then head hash, then full hash. hardlinks are ignored.",
                              bg=C["bg0"], fg=C["dim"], font=(self.F, 9), anchor="w")
        self.f_lbl.pack(fill="x")
        wrap, self.ftree = self.tree_widget(f, ("size", "date"), ("PATH", "SIZE", "MODIFIED"), (640, 100, 130))
        wrap.pack(fill="both", expand=True)
        bot = tk.Frame(f, bg=C["bg0"])
        bot.pack(fill="x", pady=6)
        self.btn(bot, "[ KEEP OLDEST, SELECT REST ]", self.finder_keep_one, C["yel"]).pack(side="left")
        self.btn(bot, "[ SELECT ALL ]", lambda: self.ftree.selection_set(list(self.f_paths)), C["blue"]).pack(
            side="left", padx=8)
        self.chk(bot, "SHRED", self.shred_var, C["orange"]).pack(side="left", padx=8)
        self.btn(bot, "[ DELETE SELECTED ]", self.finder_delete, C["red"]).pack(side="right")

    def finder_mode(self):
        m = self.fmode.get()
        self.fmin.set({"dups": "1", "old_large": "500"}.get(m, "0"))

    def finder_run(self):
        root = os.path.expanduser(self.fpath.get().strip() or HOME)
        if not os.path.isdir(root):
            self.log(f"invalid path: {root}", "err")
            return
        mode = self.fmode.get()
        try:
            min_mb = float(self.fmin.get() or 0)
            days = int(self.fdays.get() or 0)
        except ValueError:
            self.log("min size / days must be numbers.", "err")
            return
        self.stop.clear()
        self.log(f"finder [{mode}] on {root} ...", "mag")

        def cb(s):
            self.ui(lambda: self.f_lbl.configure(text="▓ " + s, fg=C["mag"]))

        def work():
            if mode == "dups":
                return finder.find_duplicates(root, int(min_mb * 1048576), self.stop, cb)
            return finder.find_misc(root, [mode], self.stop, int(min_mb * 1048576), days, cb)

        def then(res):
            if res is None:
                self.f_lbl.configure(text="interrupted.", fg=C["orange"])
                return
            self.f_mode = mode
            self.fill_finder(res)
        self.bg(work, then)

    def fill_finder(self, res):
        self.ftree.delete(*self.ftree.get_children())
        self.f_paths.clear()

        def mt(p):
            try:
                return time.strftime("%Y-%m-%d", time.localtime(os.lstat(p).st_mtime))
            except OSError:
                return ""
        if self.f_mode == "dups":
            wasted = 0
            for sz, paths in res[:600]:
                w = sz * (len(paths) - 1)
                wasted += w
                g = self.ftree.insert("", "end", text=f"{len(paths)} copies x {human(sz)}  ::  wasted {human(w)}",
                                      values=("", ""), tags=("grp",), open=True)
                for p in paths:
                    iid = self.ftree.insert(g, "end", text=p, values=(human(sz), mt(p)))
                    self.f_paths[iid] = p
            self.f_lbl.configure(text=f"{len(res)} duplicate groups :: {human(wasted)} wasted. "
                                      "select copies and delete, or use KEEP OLDEST.", fg=C["cyan"])
        else:
            items = res[self.f_mode]
            for sz, p in items[:5000]:
                iid = self.ftree.insert("", "end", text=p, values=(human(sz) if sz else "-", mt(p)))
                self.f_paths[iid] = p
            self.f_lbl.configure(text=f"{len(items)} results"
                                      + (f" :: {human(sum(s for s, _ in items))}" if self.f_mode == "old_large" else ""),
                                 fg=C["cyan"])
        self.log(f"finder done: {len(self.f_paths)} entries.", "ok")

    def finder_keep_one(self):
        if self.f_mode != "dups":
            return
        sel = []
        for g in self.ftree.get_children():
            kids = self.ftree.get_children(g)
            sel += list(kids[1:])
        self.ftree.selection_set(sel)
        self.log(f"{len(sel)} copies selected (oldest of every group kept).", "dim")

    def finder_delete(self):
        paths = [self.f_paths[i] for i in self.ftree.selection() if i in self.f_paths]
        if not paths:
            self.log("nothing selected.", "warn")
            return

        def done(freed, deleted, sizes):
            if core.DRY.is_set():
                return
            gone = {p for p in deleted if not os.path.lexists(p)}
            for iid in [i for i, p in self.f_paths.items() if p in gone]:
                if self.ftree.exists(iid):
                    self.ftree.delete(iid)
                del self.f_paths[iid]
        self.delete_paths(paths, self.shred_var.get(), done)


def run():
    App().mainloop()
