"""Elysium v2 - Tkinter UI. Isolated .elysium folder, self-repairing, school-friendly."""
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from pathlib import Path

from launcher_core import (
    CFG, MC_VERSION, FORGE_VERSION, FORGE_FULL,
    get_mc_dir, ensure_dirs, find_java, check_java_version, java_info,
    launch, list_local_versions, repair_all, proxy_reachable,
    via_proxy, INSTALLER_URL, save_config,
)

CREAM = "#FDFBF0"
GOLD = "#B9975B"
GOLD_DARK = "#A68A4A"
ENTRY_BG = "#F5F1E6"
BTN_GOLD = "#D4A651"
MUTED = "#9A958A"

TROUBLESHOOT = (
    "If you see errors like 'org.lwjgl', 'jna', 'jopt-simple' or '*.dll was not found':\n"
    "1. Hit REPAIR (re-downloads missing libraries into .elysium)\n"
    "2. Make sure Java 17+ is installed\n"
    "3. Don't delete the .elysium folder mid-download\n"
    "4. On school wifi, start web-proxy first:  npm start  (in web-proxy/)"
)


class Elysium(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Elysium 2.0")
        self.geometry("620x900")
        self.resizable(False, False)
        self.configure(bg=CREAM)

        outer = tk.Frame(self, bg=GOLD, padx=2, pady=2)
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        inner = tk.Frame(outer, bg=CREAM, highlightbackground=GOLD, highlightthickness=1)
        inner.pack(fill="both", expand=True, padx=3, pady=3)

        body = tk.Frame(inner, bg=CREAM, padx=26, pady=14)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="ELYSIUM", font=("Times New Roman", 40, "bold italic"),
                 fg="#2B2B2B", bg=CREAM).pack(pady=(2, 0))
        tk.Label(body, text="I S O L A T E D   •   S C H O O L - F R I E N D L Y",
                 font=("Arial", 8), fg="#8A8A8A", bg=CREAM).pack(pady=(0, 10))

        card = tk.Frame(body, bg="#FFFEF9", highlightbackground="#E7DCC3",
                        highlightthickness=1, padx=20, pady=16)
        card.pack(fill="x")

        # username
        tk.Label(card, text="PROFILER HANDLE", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        self.name_var = tk.StringVar(value="Steve")
        tk.Entry(card, textvariable=self.name_var, font=("Arial", 11),
                 bg=ENTRY_BG, relief="flat", highlightbackground="#E7DCC3",
                 highlightthickness=1).pack(fill="x", ipady=7, pady=(4, 10))

        # version header
        head = tk.Frame(card, bg="#FFFEF9")
        head.pack(fill="x")
        tk.Label(head, text="SYSTEM RUNTIME VERSION", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9").pack(side="left")
        self.count_lbl = tk.Label(head, text="", font=("Arial", 8),
                                  fg=MUTED, bg="#FFFEF9")
        self.count_lbl.pack(side="right")

        row1 = tk.Frame(card, bg="#FFFEF9")
        row1.pack(fill="x", pady=(4, 8))
        self.search_var = tk.StringVar()
        search = tk.Entry(row1, textvariable=self.search_var, font=("Arial", 10),
                          bg="#FFFEF9", fg="#6B6B6B", relief="solid",
                          highlightbackground="#E7DCC3", highlightthickness=1)
        search.insert(0, "Search  (e.g. 1.20, forge)")
        search.bind("<FocusIn>", lambda e: search.delete(0, "end") if "Search" in search.get() else None)
        search.bind("<KeyRelease>", lambda e: self.filter_versions())
        search.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        tk.Button(row1, text="REFRESH", font=("Arial", 9, "bold"), bg="#F0EDE6", fg="#8A8A8A",
                  relief="flat", padx=12, pady=6, command=self.on_refresh).pack(side="right")

        self.version_var = tk.StringVar(value=f"{MC_VERSION}-forge-{FORGE_VERSION}")
        drop = tk.Frame(card, bg=ENTRY_BG, highlightbackground="#E7DCC3", highlightthickness=1)
        drop.pack(fill="x", pady=(0, 8))
        self.version_entry = tk.Entry(drop, textvariable=self.version_var, font=("Arial", 11),
                                      bg=ENTRY_BG, relief="flat", state="readonly")
        self.version_entry.pack(side="left", fill="x", expand=True, ipadx=10, ipady=7)
        tk.Button(drop, text="∨", font=("Arial", 11, "bold"), bg=BTN_GOLD, fg="white",
                  relief="flat", padx=14, command=self.toggle_list).pack(side="right", fill="y")
        self.listbox_frame = tk.Frame(card, bg="#FFFEF9")
        self.listbox = tk.Listbox(self.listbox_frame, height=5, font=("Arial", 10),
                                  bg="white", relief="solid", borderwidth=1)
        self.listbox.bind("<<ListboxSelect>>", self.on_pick)
        self.all_versions: list[str] = []
        self.list_visible = False
        self.on_refresh(silent=True)

        self.forge_var = tk.BooleanVar(value=True)
        tk.Checkbutton(card, text=f"Install Forge {FORGE_FULL} if missing (recommended)",
                       variable=self.forge_var, font=("Arial", 10),
                       bg="#FFFEF9", fg="#4A4A4A", anchor="w").pack(fill="x", pady=(4, 0))
        tk.Label(card, text="Untick for pure vanilla. Forge downloads route via school proxy if blocked.",
                 font=("Arial", 8), fg=MUTED, bg="#FFFEF9", anchor="w").pack(fill="x", pady=(0, 10))

        tk.Label(card, text="JAVA EXECUTABLE (optional)", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        row2 = tk.Frame(card, bg="#FFFEF9")
        row2.pack(fill="x", pady=(4, 0))
        self.java_var = tk.StringVar(value="Auto (bundled runtime / PATH)")
        tk.Entry(row2, textvariable=self.java_var, font=("Arial", 10),
                 bg="#FFFEF9", relief="solid", highlightbackground="#E7DCC3",
                 highlightthickness=1).pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        tk.Button(row2, text="BROWSE", font=("Arial", 9, "bold"), bg="#F0EDE6", fg="#8A8A8A",
                  relief="flat", padx=12, pady=6, command=self.on_browse).pack(side="right")
        self.java_lbl = tk.Label(card, text="", font=("Arial", 8), fg=MUTED, bg="#FFFEF9", anchor="w")
        self.java_lbl.pack(fill="x", pady=(4, 0))
        self._refresh_java_label()

        # status + progress
        self.status_var = tk.StringVar(value="System cleared for operational departure.")
        tk.Label(body, textvariable=self.status_var, font=("Times New Roman", 11, "italic"),
                 fg="#2B2B2B", bg=CREAM, wraplength=520, justify="center").pack(pady=(12, 6))
        self.prog = tk.Canvas(body, height=6, bg="#EDE7D6", highlightthickness=0)
        self.prog.pack(fill="x", padx=4)
        self.prog_bar = self.prog.create_rectangle(0, 0, 10, 6, fill=BTN_GOLD, outline="")

        btnrow = tk.Frame(body, bg=CREAM)
        btnrow.pack(pady=(12, 2))
        tk.Button(btnrow, text="LAUNCH SYSTEM", font=("Arial", 12, "bold"),
                  bg=BTN_GOLD, fg="white", relief="flat", padx=26, pady=10,
                  command=self.on_launch).pack(side="left", padx=(0, 8))
        tk.Button(btnrow, text="REPAIR", font=("Arial", 10, "bold"),
                  bg="#F0EDE6", fg="#6B6B6B", relief="flat", padx=18, pady=10,
                  command=self.on_repair).pack(side="left", padx=(0, 8))
        tk.Button(btnrow, text="FOLDER", font=("Arial", 10, "bold"),
                  bg="#F0EDE6", fg="#6B6B6B", relief="flat", padx=18, pady=10,
                  command=self.on_folder).pack(side="left")

        # log console
        self.log = scrolledtext.ScrolledText(body, height=6, font=("Consolas", 8),
                                             bg="#1E1E1E", fg="#D6D6D6", relief="flat")
        self.log.pack(fill="x", pady=(10, 0))
        self.log.insert("end", "Elysium log ready.\n")
        self.log.configure(state="disabled")

        info = (f"Isolated: {get_mc_dir()}   •   "
                f"Proxy: {'ON' if CFG.get('use_proxy_for_forge') else 'OFF'}")
        tk.Label(body, text=info, font=("Arial", 7), fg=MUTED, bg=CREAM,
                 wraplength=540, justify="center").pack(pady=(6, 0))
        tk.Label(body, text="You must own Minecraft: Java Edition. Elysium never touches official .minecraft.",
                 font=("Arial", 7, "italic"), fg=MUTED, bg=CREAM,
                 wraplength=540, justify="center").pack()

        # background proxy check (non-blocking)
        threading.Thread(target=self._proxy_check, daemon=True).start()

    # ----- helpers -----
    def _proxy_check(self):
        ok = proxy_reachable()
        self.after(0, lambda: self._writelog(
            f"School proxy {'reachable at ' + CFG.get('proxy_base','')[:40] + '...' if ok else 'NOT running - direct mode (start web-proxy/npm start for blocked Forge hosts)'}\n"))

    def _writelog(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg if msg.endswith("\n") else msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_java_label(self):
        try:
            j = self.java_var.get().strip()
            exe = "" if j.startswith("Auto") else j
            found = find_java(exe)
            major = check_java_version(found)
            if major:
                self.java_lbl.config(text=f"Detected: {found}  →  Java {major}" +
                                     ("  ✓ (17+ OK)" if major >= 17 else "  ✗ NEED 17+!"),
                                     fg="#2E7D32" if major >= 17 else "#C62828")
            else:
                self.java_lbl.config(text=f"Java not verified at '{found}' - install Java 17+.", fg="#C62828")
        except Exception:
            pass

    # ----- versions -----
    def refresh_listbox(self, items):
        self.listbox.delete(0, "end")
        for v in items:
            self.listbox.insert("end", v)
        self.count_lbl.config(text=f"{len(items)} of {len(self.all_versions)}")

    def filter_versions(self):
        q = self.search_var.get().lower().replace("search", "").strip()
        if not q or "e.g." in q:
            self.refresh_listbox(self.all_versions)
        else:
            self.refresh_listbox([v for v in self.all_versions if q in v.lower()])

    def toggle_list(self):
        if self.list_visible:
            self.listbox_frame.pack_forget()
        else:
            self.listbox_frame.pack(fill="x", pady=(0, 8))
            self.listbox.pack(fill="x")
        self.list_visible = not self.list_visible

    def on_pick(self, _e):
        sel = self.listbox.curselection()
        if sel:
            self.version_var.set(self.listbox.get(sel[0]))
            self.toggle_list()

    def on_refresh(self, silent=False):
        try:
            ensure_dirs()
            self.all_versions = list_local_versions()
            self.refresh_listbox(self.all_versions)
            if self.all_versions and self.version_var.get() not in self.all_versions:
                self.version_var.set(self.all_versions[0])
            if not silent:
                self._writelog(f"Versions refreshed: {len(self.all_versions)} found in {get_mc_dir()}\n")
        except Exception as ex:
            if not silent:
                messagebox.showerror("Refresh failed", str(ex))

    def on_browse(self):
        p = filedialog.askopenfilename(title="Select java.exe / javaw.exe",
                                       filetypes=[("Java", "java*.exe"), ("All", "*.*")])
        if p:
            self.java_var.set(p)
            self._refresh_java_label()

    def on_folder(self):
        try:
            d = ensure_dirs()
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(d)])
            else:
                messagebox.showinfo("Folder", str(d))
        except Exception as ex:
            messagebox.showerror("Open folder failed", str(ex))

    # ----- progress -----
    def set_progress(self, pct, msg):
        try:
            pct = max(0, min(100, float(pct)))
        except Exception:
            pct = 0
        self.status_var.set(str(msg))
        try:
            w = max(50, self.prog.winfo_width())
            self.prog.coords(self.prog_bar, 0, 0, w * pct / 100, 6)
        except Exception:
            pass
        self._writelog(f"[{pct:5.1f}%] {msg}")
        self.update_idletasks()

    def _friendly_error(self, ex: Exception) -> str:
        s = str(ex)
        low = s.lower()
        if any(k in low for k in ("lwjgl", "jna", "jopt-simple", "not found", "dll", "natives")):
            return s + "\n\n" + TROUBLESHOOT
        if "java" in low and ("17" in s or "version" in low):
            return s + "\n\nInstall Java 17 or 21 (Temurin/Adoptium), then BROWSE to java.exe."
        if "proxy" in low or "failed" in low or "timed out" in low:
            return (s + "\n\nSchool wifi may be blocking Forge.\n"
                    "1. cd web-proxy && npm install && npm start\n"
                    "2. Keep that window open, then LAUNCH again\n"
                    "3. Or set use_proxy_for_forge=true in config.json")
        return s

    # ----- actions -----
    def on_repair(self):
        self.set_progress(2, "Repairing isolated .elysium install...")
        def worker():
            try:
                d = repair_all(progress_cb=lambda p, m: self.after(0, lambda: self.set_progress(p, m)))
                self.after(0, lambda: (self.set_progress(100, f"Repair done: {d}"),
                                        self.on_refresh(silent=True)))
            except Exception as ex:
                msg = self._friendly_error(ex)
                self.after(0, lambda: (self.set_progress(0, "Repair failed - see popup"),
                                        messagebox.showerror("Repair failed", msg)))
        threading.Thread(target=worker, daemon=True).start()

    def on_launch(self):
        user = self.name_var.get().strip() or "Steve"
        if len(user) < 3 or len(user) > 16 or not user.replace("_", "").isalnum():
            messagebox.showwarning("Handle?", "Use 3-16 letters/numbers/_ for the profile handle.")
            return
        ver = self.version_var.get().strip()
        java = self.java_var.get().strip()
        if java.startswith("Auto"):
            java = ""
        do_forge = self.forge_var.get() or "forge" in ver.lower()
        self.set_progress(2, "Starting... (isolated .elysium, never touches .minecraft)")
        def worker():
            try:
                proc = launch(user, ver, java, do_forge,
                              progress_cb=lambda p, m: self.after(0, lambda: self.set_progress(p, m)))
                self.after(0, lambda: (self.set_progress(100, f"Launched {ver} as {user} (pid {proc.pid})"),
                                        self.on_refresh(silent=True)))
            except Exception as ex:
                msg = self._friendly_error(ex)
                self.after(0, lambda: (self.set_progress(0, "Launch failed - see popup"),
                                        messagebox.showerror("Launch failed", msg)))
        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    Elysium().mainloop()
