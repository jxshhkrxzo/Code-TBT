"""Elysium v2 - Tkinter UI. Isolated .elysium folder, self-repairing, school-friendly.

Instances + Instant launch:
- Multiple named instances to choose from (each = version + handle + java + ram + fast flag)
- Each instance gets its own game folder: .elysium/instances/<name>/ (saves/mods separate)
- Shared install dir (.elysium/versions+libraries) so downloads happen ONCE
- Instant path skips re-verify/re-download and launches in ~1-2s if version is ready
"""
import json
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog
from pathlib import Path

from launcher_core import (
    CFG, MC_VERSION, FORGE_VERSION, FORGE_FULL,
    get_mc_dir, ensure_dirs, find_java, check_java_version, java_info,
    launch, list_local_versions, repair_all, proxy_reachable,
    via_proxy, INSTALLER_URL, save_config,
    version_json_path, build_offline_options,
)

CREAM = "#FDFBF0"
GOLD = "#B9975B"
GOLD_DARK = "#A68A4A"
ENTRY_BG = "#F5F1E6"
BTN_GOLD = "#D4A651"
MUTED = "#9A958A"

BASE_DIR = Path(__file__).parent
INSTANCES_FILE = BASE_DIR / "instances.json"
RAM_CHOICES = ["2G", "3G", "4G", "6G", "8G"]
DEFAULT_RAM = "4G"

TROUBLESHOOT = (
    "If you see errors like 'org.lwjgl', 'jna', 'jopt-simple' or '*.dll was not found':\n"
    "1. Hit REPAIR (re-downloads missing libraries into .elysium)\n"
    "2. Make sure Java 17+ is installed\n"
    "3. Don't delete the .elysium folder mid-download\n"
    "4. On school wifi, start web-proxy first:  npm start  (in web-proxy/)"
)

PASSWORD = "broimsafe56213"


# ---------------------------------------------------------------- instances ---
def _safe_instance_name(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in ("-", "_", " ")) else "_" for c in name.strip())
    keep = keep.strip().replace(" ", "_") or "instance"
    return keep[:32]


def default_instances() -> list[dict]:
    forge_id = f"{MC_VERSION}-forge-{FORGE_VERSION}"
    return [
        {"name": "Forge Main", "version": forge_id, "username": "Steve",
         "java": "", "use_forge": True, "ram": DEFAULT_RAM, "fast": True},
        {"name": "Vanilla Instant", "version": MC_VERSION, "username": "Steve",
         "java": "", "use_forge": False, "ram": DEFAULT_RAM, "fast": True},
        {"name": "Vanilla Safe", "version": MC_VERSION, "username": "Steve",
         "java": "", "use_forge": False, "ram": DEFAULT_RAM, "fast": False},
    ]


def load_instances() -> tuple[list[dict], str]:
    try:
        if INSTANCES_FILE.exists():
            data = json.loads(INSTANCES_FILE.read_text(encoding="utf-8"))
            insts = data.get("instances", []) if isinstance(data, dict) else []
            last = data.get("last", "") if isinstance(data, dict) else ""
            # sanitize
            clean: list[dict] = []
            for i in insts:
                if not isinstance(i, dict) or not str(i.get("name", "")).strip():
                    continue
                clean.append({
                    "name": str(i["name"])[:32],
                    "version": str(i.get("version", MC_VERSION)),
                    "username": str(i.get("username", "Steve")),
                    "java": str(i.get("java", "")),
                    "use_forge": bool(i.get("use_forge", "forge" in str(i.get("version", "")).lower())),
                    "ram": str(i.get("ram", DEFAULT_RAM)) if str(i.get("ram", DEFAULT_RAM)) in RAM_CHOICES else DEFAULT_RAM,
                    "fast": bool(i.get("fast", True)),
                })
            if clean:
                if last not in [c["name"] for c in clean]:
                    last = clean[0]["name"]
                return clean, last
    except Exception as e:
        print(f"[elysium] instances load failed ({e}), using defaults")
    insts = default_instances()
    return insts, insts[0]["name"]


def save_instances(instances: list[dict], last: str) -> None:
    try:
        INSTANCES_FILE.write_text(
            json.dumps({"instances": instances, "last": last}, indent=2),
            encoding="utf-8")
    except Exception as e:
        print(f"[elysium] instances save failed: {e}")


def get_instance_dir(name: str) -> Path:
    d = get_mc_dir() / "instances" / _safe_instance_name(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_version_ready(version_id: str) -> bool:
    """True if version can launch instantly (json + client jar present)."""
    try:
        p = version_json_path(version_id)
        if not p.exists():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        if "mainClass" not in data:
            return False
        jar = p.parent / f"{version_id}.jar"
        if not jar.exists():
            return False
        return True
    except Exception:
        return False


def instant_launch(username: str, version_id: str, java_path: str = "",
                   ram: str = DEFAULT_RAM, game_dir: Path | None = None,
                   progress_cb=None):
    """Launch WITHOUT any network verify. Raises if version not ready."""
    try:
        import minecraft_launcher_lib as mll
    except ImportError:
        raise RuntimeError("Missing dependency: run  pip install -r requirements.txt")

    if not is_version_ready(version_id):
        raise RuntimeError(f"Version '{version_id}' is not downloaded yet - untick Instant for one full install.")

    mc_dir = str(get_mc_dir())
    if game_dir is None:
        game_dir = Path(mc_dir)
    Path(game_dir).mkdir(parents=True, exist_ok=True)

    java = find_java(java_path)
    major = check_java_version(java)
    need = int(CFG.get("java_min_major", 17))
    if major == 0:
        raise RuntimeError(f"Could not verify Java at '{java}'. Install Java {need}+ first.")
    if major and major < need:
        raise RuntimeError(f"Minecraft {MC_VERSION} needs Java {need}+ (found Java {major} at {java}).")

    if progress_cb:
        progress_cb(60, f"Instant launch {version_id} ...")

    opts = build_offline_options(username or "Steve")
    opts["launcherName"] = "Elysium"
    opts["launcherVersion"] = "2.0"
    opts["gameDirectory"] = str(game_dir)
    if java_path:
        opts["java"] = java
    if ram in RAM_CHOICES:
        opts["jvmArguments"] = [f"-Xmx{ram}", "-Xms1G"]

    try:
        cmd = mll.command.get_minecraft_command(version_id, mc_dir, opts)
    except Exception as e:
        raise RuntimeError(f"Could not build launch command for '{version_id}' ({e}). Untick Instant once to repair.")
    if progress_cb:
        progress_cb(85, f"Starting {version_id}...")
    print("[elysium-instant] " + " ".join(cmd[:6]) + " ...")
    try:
        proc = subprocess.Popen(cmd, cwd=mc_dir,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise RuntimeError(f"Failed to start Java ({java}): {e}")
    return proc


def request_password():
    """Modal password gate. Returns only on success; exits instantly on failure/close."""
    pw_root = tk.Tk()
    pw_root.title("Elysium - Locked")
    pw_root.geometry("380x270")
    pw_root.resizable(False, False)
    pw_root.configure(bg=CREAM)
    # Center roughly on screen
    try:
        pw_root.update_idletasks()
        x = (pw_root.winfo_screenwidth() // 2) - (380 // 2)
        y = (pw_root.winfo_screenheight() // 2) - (270 // 2)
        pw_root.geometry(f"380x270+{x}+{y}")
    except Exception:
        pass

    outer = tk.Frame(pw_root, bg=GOLD, padx=2, pady=2)
    outer.pack(fill="both", expand=True, padx=10, pady=10)
    inner = tk.Frame(outer, bg=CREAM, padx=24, pady=18)
    inner.pack(fill="both", expand=True)

    tk.Label(inner, text="ELYSIUM", font=("Times New Roman", 22, "bold italic"),
             fg="#2B2B2B", bg=CREAM).pack(pady=(0, 2))
    tk.Label(inner, text="ENTER PASSWORD TO CONTINUE", font=("Arial", 8, "bold"),
             fg=GOLD_DARK, bg=CREAM).pack(pady=(0, 12))

    pw_var = tk.StringVar()
    entry = tk.Entry(inner, textvariable=pw_var, font=("Arial", 12),
                     bg=ENTRY_BG, relief="flat", highlightbackground="#E7DCC3",
                     highlightthickness=1, show="*")
    entry.pack(fill="x", ipady=8)
    entry.focus_set()

    show_var = tk.BooleanVar(value=False)

    def toggle_show():
        entry.config(show="" if show_var.get() else "*")

    tk.Checkbutton(inner, text="Show password", variable=show_var,
                   font=("Arial", 9), bg=CREAM, fg="#4A4A4A",
                   activebackground=CREAM, command=toggle_show).pack(anchor="w", pady=(6, 10))

    def on_submit(_event=None):
        if pw_var.get() == PASSWORD:
            pw_root.destroy()  # success -> let launcher open
        else:
            pw_root.destroy()  # wrong -> close instantly, no launcher
            sys.exit(0)

    def on_close():
        pw_root.destroy()
        sys.exit(0)

    pw_root.protocol("WM_DELETE_WINDOW", on_close)
    entry.bind("<Return>", on_submit)

    tk.Button(inner, text="UNLOCK", font=("Arial", 11, "bold"),
              bg=BTN_GOLD, fg="white", relief="flat", padx=20, pady=8,
              command=on_submit).pack(fill="x")
    pw_root.mainloop()


class Elysium(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Elysium 2.0")
        self.geometry("640x990")
        self.resizable(False, False)
        self.configure(bg=CREAM)

        # instances state (loaded before widgets so defaults flow into UI)
        self.instances, _last = load_instances()
        _cur = next((i for i in self.instances if i["name"] == _last), self.instances[0])

        outer = tk.Frame(self, bg=GOLD, padx=2, pady=2)
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        inner = tk.Frame(outer, bg=CREAM, highlightbackground=GOLD, highlightthickness=1)
        inner.pack(fill="both", expand=True, padx=3, pady=3)

        body = tk.Frame(inner, bg=CREAM, padx=26, pady=10)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="ELYSIUM", font=("Times New Roman", 36, "bold italic"),
                 fg="#2B2B2B", bg=CREAM).pack(pady=(0, 0))
        tk.Label(body, text="I S O L A T E D   •   S C H O O L - F R I E N D L Y",
                 font=("Arial", 8), fg="#8A8A8A", bg=CREAM).pack(pady=(0, 8))

        card = tk.Frame(body, bg="#FFFEF9", highlightbackground="#E7DCC3",
                        highlightthickness=1, padx=20, pady=14)
        card.pack(fill="x")

        # ---- INSTANCE selector ----
        tk.Label(card, text="INSTANCE  (pick one, plays instantly)", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        irow = tk.Frame(card, bg=ENTRY_BG, highlightbackground="#E7DCC3", highlightthickness=1)
        irow.pack(fill="x", pady=(4, 6))
        self.instance_var = tk.StringVar(value=_cur["name"])
        self.instance_entry = tk.Entry(irow, textvariable=self.instance_var, font=("Arial", 11, "bold"),
                                       bg=ENTRY_BG, relief="flat", state="readonly")
        self.instance_entry.pack(side="left", fill="x", expand=True, ipadx=10, ipady=7)
        tk.Button(irow, text="∨", font=("Arial", 11, "bold"), bg=BTN_GOLD, fg="white",
                  relief="flat", padx=14, command=self.toggle_inst_list).pack(side="right", fill="y")
        self.inst_list_frame = tk.Frame(card, bg="#FFFEF9")
        self.inst_listbox = tk.Listbox(self.inst_list_frame, height=3, font=("Arial", 10),
                                       bg="white", relief="solid", borderwidth=1)
        self.inst_listbox.bind("<<ListboxSelect>>", self.on_inst_pick)
        self.inst_visible = False

        ibtns = tk.Frame(card, bg="#FFFEF9")
        ibtns.pack(fill="x", pady=(0, 8))
        tk.Button(ibtns, text="NEW", font=("Arial", 8, "bold"), bg="#F0EDE6", fg="#6B6B6B",
                  relief="flat", padx=10, pady=4, command=self.on_inst_new).pack(side="left", padx=(0, 6))
        tk.Button(ibtns, text="SAVE", font=("Arial", 8, "bold"), bg="#F0EDE6", fg="#6B6B6B",
                  relief="flat", padx=10, pady=4, command=self.on_inst_save).pack(side="left", padx=(0, 6))
        tk.Button(ibtns, text="DEL", font=("Arial", 8, "bold"), bg="#F0EDE6", fg="#6B6B6B",
                  relief="flat", padx=10, pady=4, command=self.on_inst_delete).pack(side="left")
        self.inst_info = tk.Label(card, text="", font=("Arial", 8), fg=MUTED,
                                  bg="#FFFEF9", anchor="w")
        self.inst_info.pack(fill="x", pady=(0, 8))

        # username
        tk.Label(card, text="PROFILER HANDLE", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        self.name_var = tk.StringVar(value=_cur.get("username", "Steve"))
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

        self.version_var = tk.StringVar(value=_cur.get("version", f"{MC_VERSION}-forge-{FORGE_VERSION}"))
        drop = tk.Frame(card, bg=ENTRY_BG, highlightbackground="#E7DCC3", highlightthickness=1)
        drop.pack(fill="x", pady=(0, 8))
        self.version_entry = tk.Entry(drop, textvariable=self.version_var, font=("Arial", 11),
                                      bg=ENTRY_BG, relief="flat", state="readonly")
        self.version_entry.pack(side="left", fill="x", expand=True, ipadx=10, ipady=7)
        tk.Button(drop, text="∨", font=("Arial", 11, "bold"), bg=BTN_GOLD, fg="white",
                  relief="flat", padx=14, command=self.toggle_list).pack(side="right", fill="y")
        self.listbox_frame = tk.Frame(card, bg="#FFFEF9")
        self.listbox = tk.Listbox(self.listbox_frame, height=4, font=("Arial", 10),
                                  bg="white", relief="solid", borderwidth=1)
        self.listbox.bind("<<ListboxSelect>>", self.on_pick)
        self.all_versions: list[str] = []
        self.list_visible = False
        self.on_refresh(silent=True)

        self.forge_var = tk.BooleanVar(value=bool(_cur.get("use_forge", True)))
        tk.Checkbutton(card, text=f"Install Forge {FORGE_FULL} if missing (recommended)",
                       variable=self.forge_var, font=("Arial", 10),
                       bg="#FFFEF9", fg="#4A4A4A", anchor="w").pack(fill="x", pady=(4, 0))
        tk.Label(card, text="Untick for pure vanilla. Forge downloads route via school proxy if blocked.",
                 font=("Arial", 8), fg=MUTED, bg="#FFFEF9", anchor="w").pack(fill="x", pady=(0, 6))

        # ---- instant + RAM ----
        optrow = tk.Frame(card, bg="#FFFEF9")
        optrow.pack(fill="x", pady=(2, 0))
        self.fast_var = tk.BooleanVar(value=bool(_cur.get("fast", True)))
        tk.Checkbutton(optrow, text="⚡ Instant launch (skip re-check, play in seconds)",
                       variable=self.fast_var, font=("Arial", 10, "bold"),
                       bg="#FFFEF9", fg="#2B2B2B", anchor="w").pack(side="left")
        tk.Label(optrow, text="RAM", font=("Arial", 9, "bold"), fg=GOLD_DARK,
                 bg="#FFFEF9").pack(side="left", padx=(10, 4))
        self.ram_var = tk.StringVar(value=_cur.get("ram", DEFAULT_RAM))
        tk.OptionMenu(optrow, self.ram_var, *RAM_CHOICES).pack(side="left")
        tk.Label(card, text="Instant = no downloads, straight into the game. Untick once if files are missing/corrupt.",
                 font=("Arial", 8), fg=MUTED, bg="#FFFEF9", anchor="w").pack(fill="x", pady=(0, 8))

        tk.Label(card, text="JAVA EXECUTABLE (optional)", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        row2 = tk.Frame(card, bg="#FFFEF9")
        row2.pack(fill="x", pady=(4, 0))
        _j = _cur.get("java", "") or "Auto (bundled runtime / PATH)"
        self.java_var = tk.StringVar(value=_j)
        tk.Entry(row2, textvariable=self.java_var, font=("Arial", 10),
                 bg="#FFFEF9", relief="solid", highlightbackground="#E7DCC3",
                 highlightthickness=1).pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        tk.Button(row2, text="BROWSE", font=("Arial", 9, "bold"), bg="#F0EDE6", fg="#8A8A8A",
                  relief="flat", padx=12, pady=6, command=self.on_browse).pack(side="right")
        self.java_lbl = tk.Label(card, text="", font=("Arial", 8), fg=MUTED, bg="#FFFEF9", anchor="w")
        self.java_lbl.pack(fill="x", pady=(4, 0))
        self._refresh_java_label()

        # status + progress
        self.status_var = tk.StringVar(value="Pick an instance, hit PLAY. Instant = seconds.")
        tk.Label(body, textvariable=self.status_var, font=("Times New Roman", 11, "italic"),
                 fg="#2B2B2B", bg=CREAM, wraplength=520, justify="center").pack(pady=(10, 6))
        self.prog = tk.Canvas(body, height=6, bg="#EDE7D6", highlightthickness=0)
        self.prog.pack(fill="x", padx=4)
        self.prog_bar = self.prog.create_rectangle(0, 0, 10, 6, fill=BTN_GOLD, outline="")

        btnrow = tk.Frame(body, bg=CREAM)
        btnrow.pack(pady=(10, 2))
        tk.Button(btnrow, text="⚡ PLAY INSTANT", font=("Arial", 12, "bold"),
                  bg=BTN_GOLD, fg="white", relief="flat", padx=22, pady=10,
                  command=self.on_launch).pack(side="left", padx=(0, 8))
        tk.Button(btnrow, text="REPAIR", font=("Arial", 10, "bold"),
                  bg="#F0EDE6", fg="#6B6B6B", relief="flat", padx=14, pady=10,
                  command=self.on_repair).pack(side="left", padx=(0, 8))
        tk.Button(btnrow, text="FOLDER", font=("Arial", 10, "bold"),
                  bg="#F0EDE6", fg="#6B6B6B", relief="flat", padx=14, pady=10,
                  command=self.on_folder).pack(side="left")

        # log console
        self.log = scrolledtext.ScrolledText(body, height=4, font=("Consolas", 8),
                                             bg="#1E1E1E", fg="#D6D6D6", relief="flat")
        self.log.pack(fill="x", pady=(8, 0))
        self.log.insert("end", "Elysium log ready. Instant mode skips downloads.\n")
        self.log.configure(state="disabled")

        info = (f"Isolated: {get_mc_dir()}   •   "
                f"Proxy: {'ON' if CFG.get('use_proxy_for_forge') else 'OFF'}")
        tk.Label(body, text=info, font=("Arial", 7), fg=MUTED, bg=CREAM,
                 wraplength=540, justify="center").pack(pady=(6, 0))
        tk.Label(body, text="You must own Minecraft: Java Edition. Elysium never touches official .minecraft.",
                 font=("Arial", 7, "italic"), fg=MUTED, bg=CREAM,
                 wraplength=540, justify="center").pack()

        # init instance list + background warmup (non-blocking so UI opens fast)
        self.refresh_inst_listbox()
        self._apply_instance(_cur["name"], silent=True)
        threading.Thread(target=self._warmup, daemon=True).start()
        threading.Thread(target=self._proxy_check, daemon=True).start()

    # ----- background -----
    def _warmup(self):
        try:
            ensure_dirs()
            for inst in self.instances:
                try:
                    get_instance_dir(inst["name"])
                except Exception:
                    pass
            # warm java detection cache + version list
            try:
                find_java("")
            except Exception:
                pass
            self.after(0, lambda: self._writelog("Ready. Shared files kept, instances isolated.\n"))
        except Exception:
            pass

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

    # ----- instances -----
    def _find_instance(self, name: str) -> dict | None:
        for i in self.instances:
            if i["name"] == name:
                return i
        return None

    def refresh_inst_listbox(self):
        self.inst_listbox.delete(0, "end")
        for i in self.instances:
            tag = "  ⚡" if i.get("fast", True) else ""
            self.inst_listbox.insert("end", f"{i['name']}  [{i.get('version','?')}]{tag}")
        folder_hint = str(get_mc_dir() / "instances")
        self.inst_info.config(text=f"{len(self.instances)} instance(s)  •  saves in: {folder_hint}")

    def toggle_inst_list(self):
        if self.inst_visible:
            self.inst_list_frame.pack_forget()
        else:
            self.inst_list_frame.pack(fill="x", pady=(0, 6))
            self.inst_listbox.pack(fill="x")
        self.inst_visible = not self.inst_visible

    def on_inst_pick(self, _e):
        sel = self.inst_listbox.curselection()
        if not sel:
            return
        # entry shows "Name  [version]  ⚡" -> recover name
        raw = self.inst_listbox.get(sel[0])
        name = raw.split("  [")[0].strip()
        if self.inst_visible:
            self.toggle_inst_list()
        self._apply_instance(name)

    def _apply_instance(self, name: str, silent=False):
        inst = self._find_instance(name)
        if not inst:
            return
        self.instance_var.set(inst["name"])
        self.name_var.set(inst.get("username", "Steve"))
        self.version_var.set(inst.get("version", MC_VERSION))
        self.java_var.set(inst.get("java", "") or "Auto (bundled runtime / PATH)")
        self.forge_var.set(bool(inst.get("use_forge", True)))
        self.fast_var.set(bool(inst.get("fast", True)))
        rv = inst.get("ram", DEFAULT_RAM)
        self.ram_var.set(rv if rv in RAM_CHOICES else DEFAULT_RAM)
        self._refresh_java_label()
        save_instances(self.instances, inst["name"])
        if not silent:
            ready = "ready ⚡" if is_version_ready(inst.get("version", "")) else "needs one full install"
            self._writelog(f"Instance '{inst['name']}' selected ({inst.get('version','?')}, {ready})\n")
            self.status_var.set(f"{inst['name']}: {inst.get('version','?')} ({ready})")

    def _collect_to_instance(self, inst: dict):
        inst["username"] = self.name_var.get().strip() or "Steve"
        inst["version"] = self.version_var.get().strip() or MC_VERSION
        j = self.java_var.get().strip()
        inst["java"] = "" if j.startswith("Auto") else j
        inst["use_forge"] = bool(self.forge_var.get())
        inst["ram"] = self.ram_var.get() if self.ram_var.get() in RAM_CHOICES else DEFAULT_RAM
        inst["fast"] = bool(self.fast_var.get())

    def on_inst_new(self):
        name = simpledialog.askstring("New instance", "Instance name (e.g. Modded, Speedrun, School):",
                                      parent=self)
        if not name or not name.strip():
            return
        name = name.strip()[:32]
        if self._find_instance(name):
            messagebox.showwarning("Exists", f"Instance '{name}' already exists.")
            return
        inst = {"name": name, "version": self.version_var.get().strip() or MC_VERSION,
                "username": self.name_var.get().strip() or "Steve",
                "java": "", "use_forge": self.forge_var.get(),
                "ram": self.ram_var.get(), "fast": True}
        # java: copy current unless Auto
        j = self.java_var.get().strip()
        inst["java"] = "" if j.startswith("Auto") else j
        self.instances.append(inst)
        save_instances(self.instances, name)
        self.refresh_inst_listbox()
        self._apply_instance(name)
        try:
            get_instance_dir(name)
        except Exception:
            pass

    def on_inst_save(self):
        name = self.instance_var.get().strip()
        inst = self._find_instance(name)
        if not inst:
            messagebox.showwarning("No instance", "Pick or create an instance first.")
            return
        self._collect_to_instance(inst)
        save_instances(self.instances, name)
        self.refresh_inst_listbox()
        self._writelog(f"Instance '{name}' saved.\n")

    def on_inst_delete(self):
        name = self.instance_var.get().strip()
        if len(self.instances) <= 1:
            messagebox.showwarning("Keep one", "You need at least one instance.")
            return
        if not messagebox.askyesno("Delete?", f"Delete instance '{name}'?\n(Game folder is kept, only the profile is removed.)"):
            return
        self.instances = [i for i in self.instances if i["name"] != name]
        save_instances(self.instances, self.instances[0]["name"])
        self.refresh_inst_listbox()
        self._apply_instance(self.instances[0]["name"])

    # ----- versions -----
    def refresh_listbox(self, items):
        self.listbox.delete(0, "end")
        for v in items:
            mark = "  ⚡ ready" if is_version_ready(v) else ""
            self.listbox.insert("end", v + mark)
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
            raw = self.listbox.get(sel[0]).replace("  ⚡ ready", "").strip()
            self.version_var.set(raw)
            if "forge" in raw.lower():
                self.forge_var.set(True)
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
            d = get_instance_dir(self.instance_var.get().strip() or "default")
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
        inst_name = self.instance_var.get().strip() or "default"
        inst = self._find_instance(inst_name)
        if not inst:
            inst = {"name": inst_name}
            self.instances.append(inst)
        self._collect_to_instance(inst)
        inst["name"] = inst_name
        inst["username"] = user
        save_instances(self.instances, inst_name)
        self.refresh_inst_listbox()

        ver = self.version_var.get().strip()
        java = self.java_var.get().strip()
        if java.startswith("Auto"):
            java = ""
        ram = self.ram_var.get() if self.ram_var.get() in RAM_CHOICES else DEFAULT_RAM
        want_fast = bool(self.fast_var.get())
        do_forge = self.forge_var.get() or "forge" in ver.lower()

        # INSTANT PATH: no downloads, straight to game
        if want_fast and is_version_ready(ver):
            self.set_progress(30, f"⚡ Instant launch '{inst_name}' ({ver}) - no waiting...")
            def fast_worker():
                try:
                    gdir = get_instance_dir(inst_name)
                    proc = instant_launch(user, ver, java, ram, gdir,
                                          progress_cb=lambda p, m: self.after(0, lambda: self.set_progress(p, m)))
                    self.after(0, lambda: (self.set_progress(100, f"Launched {ver} as {user} (pid {proc.pid})"),
                                            self._writelog(f"⚡ Instant: '{inst_name}' -> {gdir}\n")))
                except Exception as ex:
                    msg = self._friendly_error(ex)
                    self.after(0, lambda: (self.set_progress(0, "Instant failed - see popup"),
                                            messagebox.showerror("Instant launch failed", msg + "\n\nTip: untick Instant once to do a full install.")))
            threading.Thread(target=fast_worker, daemon=True).start()
            return

        # FULL PATH (first time, or user unticked Instant): verifies + downloads, then fast next time
        reason = "first install" if not is_version_ready(ver) else "full re-check requested"
        self.set_progress(2, f"Full install ({reason})... next plays will be instant.")
        def worker():
            try:
                proc = launch(user, ver, java, do_forge,
                              progress_cb=lambda p, m: self.after(0, lambda: self.set_progress(p, m)))
                # make sure instance folder exists for next instant runs
                try:
                    get_instance_dir(inst_name)
                except Exception:
                    pass
                self.after(0, lambda: (self.set_progress(100, f"Launched {ver} as {user} (pid {proc.pid}) - next launch will be instant ⚡"),
                                        self.on_refresh(silent=True)))
            except Exception as ex:
                msg = self._friendly_error(ex)
                self.after(0, lambda: (self.set_progress(0, "Launch failed - see popup"),
                                        messagebox.showerror("Launch failed", msg)))
        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    request_password()
    Elysium().mainloop()
