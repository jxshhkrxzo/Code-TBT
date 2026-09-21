"""Elysium v2 - Tkinter UI. Isolated .elysium folder, self-repairing, school-friendly.

Instances + Instant launch:
- Multiple named instances to choose from (each = version + handle + java + ram + fast flag)
- Each instance gets its own game folder: .elysium/instances/<name>/ (saves/mods separate)
- Shared install dir (.elysium/versions+libraries) so downloads happen ONCE
- Instant path skips re-verify/re-download and launches in ~1-2s if version is ready
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from pathlib import Path

from launcher_core import (
    CFG, MC_VERSION, FORGE_VERSION, FORGE_FULL,
    get_mc_dir, ensure_dirs, find_java, check_java_version, java_info,
    launch, list_local_versions, repair_all, proxy_reachable,
    via_proxy, INSTALLER_URL, save_config,
    version_json_path, build_offline_options,
    install_vanilla, clean_natives, verify_version,
)

CREAM = "#FDFBF0"
GOLD = "#B9975B"
GOLD_DARK = "#A68A4A"
ENTRY_BG = "#F5F1E6"
BTN_GOLD = "#D4A651"
MUTED = "#9A958A"

BASE_DIR = Path(__file__).parent
INSTANCES_FILE = BASE_DIR / "instances.json"
RAM_CHOICES = ["2G", "3G", "4G", "6G", "8G", "10G", "12G", "14G", "16G"]
DEFAULT_RAM = "4G"

# SKLauncher-imported folder: <launcher>/excaliber/ is a gameDir
# (contains mods/, saves/, config/, options.txt ...). Treated like SK's
# instances/<name>/ folder - game files live there, shared
# versions/libraries/assets stay in .elysium (like SK's shared dirs).
EXCALIBER_DIR = BASE_DIR / "excaliber"
EXCALIBER_NAME = "Excaliber"
EXCALIBER_FALLBACK_VERSION = "1.20.1-forge-47.4.10"


def detect_excaliber_version() -> str:
    """Legacy wrapper - now delegates to the generic per-folder detector."""
    return detect_version_for_gamedir(EXCALIBER_DIR, EXCALIBER_FALLBACK_VERSION)


def detect_excaliber_username() -> str:
    """Legacy wrapper - now delegates to the generic per-folder detector."""
    return detect_username_for_gamedir(EXCALIBER_DIR)


def forge_full_from_version(version_id: str) -> str | None:
    """'1.20.1-forge-47.4.10' -> '1.20.1-47.4.10'. None if not a forge id."""
    m = re.match(r"^(\d+\.\d+(?:\.\d+)?)-forge-(\d+\.\d+\.\d+(?:\.\d+)?)$",
                 version_id.strip())
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


# ------------------------------------------------- generic SK import helpers ---
# Any folder that looks like a Minecraft gameDir (mods/config/saves/...) can be
# dropped into .elysium/instances/<Name>/ (or picked via IMPORT) and Elysium
# treats it like an SKLauncher instances/<name>/ folder: mods/saves/config /
# resourcepacks / shaderpacks stay in place, shared versions+libraries+assets
# stay in .elysium. Version/RAM/username are auto-detected per folder so the
# pack launches "just normal" instead of crashing on a wrong Forge build.
SK_GAMEDIR_MARKERS = ("mods", "config", "resourcepacks", "saves",
                      "shaderpacks", "options.txt", "usernamecache.json")


def is_gamedir_folder(p: Path) -> bool:
    """True if a folder looks like an SK-style gameDir (not an empty dir)."""
    try:
        if not p.is_dir():
            return False
        # needs at least one real marker; a lone empty mods/ doesn't count
        for m in SK_GAMEDIR_MARKERS:
            mp = p / m
            if mp.is_dir():
                try:
                    if any(mp.iterdir()):
                        return True
                except Exception:
                    return True
            elif mp.is_file():
                return True
        return False
    except Exception:
        return False


def detect_version_for_gamedir(gdir: Path, fallback: str = "") -> str:
    """Best-effort MC+loader id (e.g. 1.20.1-forge-47.4.10) for any gameDir.

    1. logs/latest.log (or debug.log) --version line from the last good run
    2. newest crash-report's "Launched Version:" line
    3. fallback (caller's default, usually the pinned Forge id)
    """
    try:
        for logname in ("latest.log", "debug.log"):
            lp = gdir / "logs" / logname
            if lp.exists():
                try:
                    txt = lp.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r"--version,\s*([0-9A-Za-z.\-_]+)", txt)
                    if m and ("forge" in m.group(1).lower() or
                              "fabric" in m.group(1).lower() or
                              "quilt" in m.group(1).lower() or
                              re.match(r"^\d+\.\d+", m.group(1))):
                        return m.group(1).strip()
                    mf = re.search(r"--fml\.forgeVersion,\s*([0-9.]+)", txt)
                    mm = re.search(r"--fml\.mcVersion,\s*([0-9.]+)", txt)
                    if mf and mm:
                        return f"{mm.group(1).strip()}-forge-{mf.group(1).strip()}"
                except Exception:
                    pass
        cdir = gdir / "crash-reports"
        if cdir.exists():
            try:
                reps = sorted(cdir.glob("*.txt"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
                for rp in reps[:3]:
                    try:
                        txt = rp.read_text(encoding="utf-8", errors="ignore")
                        m = re.search(r"Launched Version:\s*([0-9A-Za-z.\-_]+)",
                                      txt)
                        if m:
                            return m.group(1).strip()
                    except Exception:
                        continue
            except Exception:
                pass
    except Exception:
        pass
    return fallback or EXCALIBER_FALLBACK_VERSION


def detect_username_for_gamedir(gdir: Path) -> str:
    """Pick up the SK username so an imported pack runs 'like SK launcher'."""
    try:
        p = gdir / "usernamecache.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(data, dict) and data:
                name = next(iter(data.values()))
                if isinstance(name, str) and name.strip():
                    return name.strip()[:16]
    except Exception:
        pass
    return "Steve"


def suggest_ram_for_gamedir(gdir: Path) -> str:
    """Heavy SK packs OOM-crash on 2-4G. Scale RAM to pack size."""
    try:
        nmods = 0
        npacks = 0
        mp = gdir / "mods"
        if mp.is_dir():
            nmods = sum(1 for c in mp.iterdir()
                        if c.is_file() and c.suffix.lower() == ".jar")
        rp = gdir / "resourcepacks"
        if rp.is_dir():
            npacks = sum(1 for c in rp.iterdir() if c.name.lower() != ".ds_store"
                         and not c.name.endswith(".txt"))
        if nmods >= 200 or npacks >= 30:
            return "8G"
        if nmods >= 100 or npacks >= 15:
            return "6G"
        if nmods >= 40:
            return "4G"
    except Exception:
        pass
    return DEFAULT_RAM


def folder_name_to_instance_name(folder: Path) -> str:
    """'My_SK_Pack' -> 'My SK Pack' (safe, human-readable, unique-able)."""
    name = folder.name.strip().replace("_", " ") or "instance"
    name = re.sub(r"\s+", " ", name).strip()[:32] or "instance"
    return name


def scan_instance_folders() -> list[Path]:
    """All SK-style gameDir folders inside .elysium/instances/."""
    out: list[Path] = []
    try:
        root = get_mc_dir() / "instances"
        if root.exists():
            for child in sorted(root.iterdir()):
                try:
                    if child.is_dir() and is_gamedir_folder(child):
                        out.append(child)
                except Exception:
                    continue
    except Exception:
        pass
    return out


def java_need_for_version(version_id: str) -> int:
    """MC <1.17 needs Java 8, 1.17 needs 16, >=1.18 needs 17."""
    try:
        m = re.search(r"(\d+)\.(\d+)", version_id or "")
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            if major == 1 and minor <= 16:
                return 8
            if major == 1 and minor == 17:
                return 16
            return 17
    except Exception:
        pass
    try:
        return int(CFG.get("java_min_major", 17))
    except Exception:
        return 17


# --------------------------------------- title/music + shader crash helpers ---
def _parse_pack_list_from_log_line(line: str) -> list[str]:
    """Extract ['file/Excalibur_....zip', ...] from an 'Added resource packs' line."""
    packs: list[str] = []
    try:
        m = re.search(r"Added resource packs:\s*\[(.*)\]\s*$", line)
        if not m:
            return packs
        inner = m.group(1)
        # entries are comma-separated; built-ins have no '/' (vanilla,
        # mod_resources, fabric, continuity:...) - only file/... are real packs
        for part in inner.split(","):
            part = part.strip()
            if part.startswith("file/"):
                packs.append(part[len("file/"):].strip())
    except Exception:
        pass
    return packs


def last_good_pack_list(gdir: Path) -> list[str]:
    """Resource-pack order from the last run that actually loaded packs."""
    try:
        lp = gdir / "logs" / "latest.log"
        if lp.exists():
            lines = lp.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in reversed(lines):
                if "Added resource packs:" in line:
                    packs = _parse_pack_list_from_log_line(line)
                    if packs:
                        return packs
    except Exception:
        pass
    return []


def _quote_pack_entry(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ensure_resourcepacks_enabled(gdir: Path, progress_cb=None) -> str:
    """Restore the SK pack's title-screen/music packs when options.txt is wiped.

    Symptom fixed: Excaliber shows vanilla title + no music because
    options.txt says resourcePacks:[] even though resourcepacks/ holds 50+
    zips (incl. '! The Knight's Journey - Menu'). Minecraft wipes the list
    after a crash, so the next boot looks "like nothing". If the list is
    empty but packs exist on disk, re-enable the last good order from
    logs/latest.log (exact SK order), else enable every pack on disk.
    Returns a human-readable note ('' when nothing needed).
    """
    try:
        rp_dir = gdir / "resourcepacks"
        if not rp_dir.is_dir():
            return ""
        on_disk = [c.name for c in rp_dir.iterdir()
                   if c.name.lower() != ".ds_store" and not c.name.endswith(".txt")]
        if not on_disk:
            return ""
        opt = gdir / "options.txt"
        if not opt.exists():
            return ""
        try:
            txt = opt.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        m = re.search(r"^resourcePacks:\[(.*)\]\s*$", txt, re.M)
        if not m:
            return ""
        current = m.group(1).strip()
        if current not in ("", "[]"):
            return ""  # packs already enabled - leave SK order alone
        packs = last_good_pack_list(gdir)
        if not packs:
            # fallback: everything on disk, menu/excalibur packs first so the
            # custom title + music win even without a log
            def sort_key(n: str):
                low = n.lower()
                prio = 2
                if "knight" in low and "menu" in low:
                    prio = 0
                elif "excalibur" in low or "clarent" in low:
                    prio = 1
                return (prio, low)
            packs = sorted(on_disk, key=sort_key)
        else:
            # keep only packs still on disk (user may have deleted some)
            diskset = set(on_disk)
            packs = [p for p in packs if p in diskset]
            if not packs:
                packs = sorted(on_disk)
        # backup once so a bad write never loses the SK original
        try:
            bak = gdir / "options.txt.elysium.bak"
            if not bak.exists():
                shutil.copyfile(opt, bak)
        except Exception:
            pass
        entries = ",".join(_quote_pack_entry("file/" + p) for p in packs)
        new_txt = re.sub(r"^resourcePacks:\[.*\]\s*$", f"resourcePacks:[{entries}]",
                         txt, count=1, flags=re.M)
        # clear stale incompatible list so MC re-checks instead of disabling
        new_txt = re.sub(r"^incompatibleResourcePacks:\[.*\]\s*$",
                         "incompatibleResourcePacks:[]", new_txt, count=1, flags=re.M)
        opt.write_text(new_txt, encoding="utf-8")
        msg = (f"Restored {len(packs)} resource packs (title + music) "
               f"into options.txt")
        if progress_cb:
            try:
                progress_cb(12, msg)
            except Exception:
                pass
        print(f"[elysium] {msg} for {gdir.name}")
        return msg
    except Exception as e:
        print(f"[elysium] pack restore skipped ({e})")
        return ""


def is_shader_crash_text(txt: str) -> bool:
    low = txt.lower()
    if "elytratrims_gateway.json" in low:
        return True
    if "could not reload shaders" in low:
        return True
    return False


def recent_shader_crashes(gdir: Path, limit: int = 3) -> list[Path]:
    """Newest crash reports that are Oculus/shader reload crashes."""
    out: list[Path] = []
    try:
        cdir = gdir / "crash-reports"
        if not cdir.exists():
            return out
        reps = sorted(cdir.glob("*.txt"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        for rp in reps:
            try:
                # ignore crashes older than 14 days - stale history
                if time.time() - rp.stat().st_mtime > 14 * 86400:
                    continue
                txt = rp.read_text(encoding="utf-8", errors="ignore")
                if is_shader_crash_text(txt) and "shaderpack" in txt.lower():
                    out.append(rp)
            except Exception:
                continue
    except Exception:
        pass
    return out


def ensure_safe_shader_state(gdir: Path, force_safe: bool = False,
                             progress_cb=None) -> str:
    """Stop the ElytraTrims+Oculus+shader crash loop WITHOUT forcing a dull title.

    Known modpack bug: Elytra Trims 3.x + Oculus/Iris + any shaderpack
    (photon etc.) crashes on resource reload with
    'Invalid shaders/core/elytratrims_gateway.json'.

    Behaviour (fixed):
    - Safe mode UNCHECKED (default) -> NEVER touches oculus/iris.properties.
      Shaders stay exactly as the pack set them (e.g. photon ON) so the
      title boots pretty (bright castle panorama) instead of the flat grey
      no-shader look. If recent shader crashes exist we only return a
      warning string so the UI can suggest Safe mode - no files are changed.
    - Safe mode CHECKED -> back up config/oculus.properties (once) and set
      enableShaders=false so the game boots to the custom title instead of
      crashing. The shader file itself is kept - re-enable shaders in-game
      once past the title if wanted, or just uncheck Safe mode next launch
      to restore the pretty title.
    Returns a human-readable note ('' when nothing needed).
    """
    try:
        mods = gdir / "mods"
        has_elytra = False
        has_oculus = False
        try:
            if mods.is_dir():
                for c in mods.iterdir():
                    low = c.name.lower()
                    if "elytratrims" in low:
                        has_elytra = True
                    if "oculus" in low or low.startswith("iris-"):
                        has_oculus = True
        except Exception:
            pass
        if not (has_elytra and has_oculus):
            return ""
        crashes = recent_shader_crashes(gdir)
        if not force_safe:
            # Never auto-disable: that is what permanently forced the dull
            # grey title (shaders OFF) even when the user wants the pretty
            # shader title. Instead, restore shaders ON when a previous
            # auto-disable (or stale Safe-mode run) left them OFF, so the
            # next boot shows the bright castle panorama again.
            try:
                for cfg_name in ("oculus.properties", "iris.properties"):
                    prop = gdir / "config" / cfg_name
                    if not prop.exists():
                        continue
                    try:
                        txt = prop.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    if "enableShaders=false" in txt:
                        prop.write_text(
                            txt.replace("enableShaders=false",
                                        "enableShaders=true"),
                            encoding="utf-8")
                        msg = (f"Restored shaders ON in {cfg_name} "
                               f"(pretty title back); tick Safe mode only "
                               f"if the ElytraTrims shader crash returns")
                        if progress_cb:
                            try:
                                progress_cb(11, msg)
                            except Exception:
                                pass
                        print(f"[elysium] {msg}")
                        break
            except Exception as e:
                print(f"[elysium] shader restore skipped ({e})")
            if crashes and progress_cb:
                try:
                    progress_cb(
                        11,
                        f"Note: {len(crashes)} recent shader crash(es) seen "
                        f"(ElytraTrims+Oculus+photon) - shaders left ON for "
                        f"the pretty title; tick Safe mode if it crashes again")
                except Exception:
                    pass
            return ""
        for cfg_name in ("oculus.properties", "iris.properties"):
            prop = gdir / "config" / cfg_name
            if not prop.exists():
                continue
            try:
                txt = prop.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "enableShaders=true" not in txt:
                continue
            try:
                bak = prop.parent / (prop.name + ".elysium.bak")
                if not bak.exists():
                    shutil.copyfile(prop, bak)
            except Exception:
                pass
            txt = txt.replace("enableShaders=true", "enableShaders=false")
            prop.write_text(txt, encoding="utf-8")
            reason = (f"Safe mode: disabled shaders in {cfg_name} (manual) - "
                      f"game will boot to title; re-enable shaders in Video Settings")
            if progress_cb:
                try:
                    progress_cb(11, reason)
                except Exception:
                    pass
            print(f"[elysium] {reason}")
            return reason
    except Exception as e:
        print(f"[elysium] shader guard skipped ({e})")
    return ""

TROUBLESHOOT = (
    "If you see errors like 'org.lwjgl', 'jna', 'jopt-simple' or '*.dll was not found':\n"
    "1. Hit REPAIR (re-downloads missing libraries into .elysium)\n"
    "2. Make sure Java 17+ is installed\n"
    "3. Don't delete the .elysium folder mid-download\n"
    "4. On school wifi, start web-proxy first:  npm start  (in web-proxy/)"
)

PASSWORD = "sewfink123"


# ---------------------------------------------------------------- instances ---
def _safe_instance_name(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in ("-", "_", " ")) else "_" for c in name.strip())
    keep = keep.strip().replace(" ", "_") or "instance"
    return keep[:32]


def default_instances() -> list[dict]:
    forge_id = f"{MC_VERSION}-forge-{FORGE_VERSION}"
    insts = [
        {"name": "Forge Main", "version": forge_id, "username": "Steve",
         "java": "", "use_forge": True, "ram": DEFAULT_RAM, "fast": True},
        {"name": "Vanilla Instant", "version": MC_VERSION, "username": "Steve",
         "java": "", "use_forge": False, "ram": DEFAULT_RAM, "fast": True},
        {"name": "Vanilla Safe", "version": MC_VERSION, "username": "Steve",
         "java": "", "use_forge": False, "ram": DEFAULT_RAM, "fast": False},
    ]
    # SKLauncher import: first run already shows Excaliber as a normal instance.
    if EXCALIBER_DIR.exists() and is_gamedir_folder(EXCALIBER_DIR):
        insts.insert(0, {"name": EXCALIBER_NAME,
                         "version": detect_version_for_gamedir(
                             EXCALIBER_DIR, EXCALIBER_FALLBACK_VERSION),
                         "username": detect_username_for_gamedir(EXCALIBER_DIR),
                         "java": "", "use_forge": True,
                         "ram": suggest_ram_for_gamedir(EXCALIBER_DIR),
                         "fast": True, "gameDir": str(EXCALIBER_DIR)})
    # Any other SK folder already sitting in .elysium/instances/ works too.
    try:
        for folder in scan_instance_folders():
            name = folder_name_to_instance_name(folder)
            if any(c["name"].lower() == name.lower() for c in insts):
                continue
            insts.insert(0, {
                "name": name,
                "version": detect_version_for_gamedir(
                    folder, f"{MC_VERSION}-forge-{FORGE_VERSION}"),
                "username": detect_username_for_gamedir(folder),
                "java": "", "use_forge": True,
                "ram": suggest_ram_for_gamedir(folder),
                "fast": True, "gameDir": str(folder)})
    except Exception:
        pass
    return insts


def auto_import_missing(clean: list[dict]) -> bool:
    """Create instance entries for SK folders with no entry yet.

    Covers: <launcher>/excaliber/ (legacy) + any folder dropped into
    .elysium/instances/<Name>/ (the requested SKLauncher workflow).
    Returns True when the list changed.
    """
    changed = False
    try:
        if EXCALIBER_DIR.exists() and is_gamedir_folder(EXCALIBER_DIR) and not any(
                c["name"].lower() == EXCALIBER_NAME.lower() for c in clean):
            clean.insert(0, {"name": EXCALIBER_NAME,
                             "version": detect_version_for_gamedir(
                                 EXCALIBER_DIR, EXCALIBER_FALLBACK_VERSION),
                             "username": detect_username_for_gamedir(EXCALIBER_DIR),
                             "java": "", "use_forge": True,
                             "ram": suggest_ram_for_gamedir(EXCALIBER_DIR),
                             "fast": True, "gameDir": str(EXCALIBER_DIR)})
            changed = True
    except Exception:
        pass
    try:
        known_dirs = set()
        known_names = set()
        try:
            inst_root = get_mc_dir() / "instances"
        except Exception:
            inst_root = None
        for c in clean:
            try:
                known_names.add(str(c.get("name", "")).strip().lower())
                gd = str(c.get("gameDir", "")).strip()
                if gd:
                    p = Path(gd)
                    if not p.is_absolute():
                        p = BASE_DIR / gd
                    known_dirs.add(os.path.normcase(str(p.resolve(strict=False))))
                elif inst_root is not None:
                    # normal isolated instance already owns
                    # .elysium/instances/<safe_name>/ - never double-import it
                    implicit = inst_root / _safe_instance_name(str(c.get("name", "default")))
                    known_dirs.add(os.path.normcase(str(implicit.resolve(strict=False))))
                    known_names.add(_safe_instance_name(str(c.get("name", ""))).lower())
                    known_names.add(folder_name_to_instance_name(implicit).lower())
            except Exception:
                continue
        for folder in scan_instance_folders():
            try:
                norm = os.path.normcase(str(folder.resolve(strict=False)))
            except Exception:
                norm = os.path.normcase(str(folder))
            if norm in known_dirs:
                continue
            # Elysium's own isolated instance folders are owned by name even
            # when the JSON entry stores no gameDir key - skip those too.
            if (folder_name_to_instance_name(folder).lower() in known_names
                    or folder.name.lower() in known_names
                    or _safe_instance_name(folder_name_to_instance_name(folder)).lower() in known_names):
                continue
            name = folder_name_to_instance_name(folder)
            base = name
            n = 2
            while any(c["name"].lower() == name.lower() for c in clean):
                name = f"{base} {n}"[:32]
                n += 1
            ver = detect_version_for_gamedir(
                folder, f"{MC_VERSION}-forge-{FORGE_VERSION}")
            low = ver.lower()
            clean.insert(0, {
                "name": name, "version": ver,
                "username": detect_username_for_gamedir(folder),
                "java": "",
                "use_forge": bool("forge" in low or "fabric" in low or "quilt" in low),
                "ram": suggest_ram_for_gamedir(folder),
                "fast": True, "gameDir": str(folder)})
            known_dirs.add(norm)
            changed = True
            print(f"[elysium] auto-imported SK folder '{folder.name}' "
                  f"as instance '{name}' ({ver})")
    except Exception as e:
        print(f"[elysium] auto-import scan skipped ({e})")
    return changed


def _resolve_stored_gamedir(raw: object) -> str:
    """Keep per-instance custom gameDir (used by the Excaliber SK import)."""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    s = raw.strip()
    try:
        p = Path(s)
        if not p.is_absolute():
            # stored relative (e.g. "excaliber") -> resolve against launcher dir
            p = BASE_DIR / s
        # Only keep it if it looks like a gameDir (has mods/saves/config or is excaliber)
        if p.exists():
            return str(p)
        # keep excaliber path even if temporarily missing so settings survive
        if p == EXCALIBER_DIR or s.lower() in ("excaliber", "./excaliber"):
            return str(EXCALIBER_DIR)
    except Exception:
        pass
    return ""


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
                entry = {
                    "name": str(i["name"])[:32],
                    "version": str(i.get("version", MC_VERSION)),
                    "username": str(i.get("username", "Steve")),
                    "java": str(i.get("java", "")),
                    "use_forge": bool(i.get("use_forge", "forge" in str(i.get("version", "")).lower())),
                    "ram": str(i.get("ram", DEFAULT_RAM)) if str(i.get("ram", DEFAULT_RAM)) in RAM_CHOICES else DEFAULT_RAM,
                    "fast": bool(i.get("fast", True)),
                }
                gd = _resolve_stored_gamedir(i.get("gameDir", i.get("game_dir", "")))
                if gd:
                    entry["gameDir"] = gd
                clean.append(entry)
            if clean:
                # auto-import: excaliber/ + any folder dropped into
                # .elysium/instances/<Name>/ becomes a normal instance
                # (SKLauncher instances/<name>/ layout = gameDir).
                if auto_import_missing(clean):
                    try:
                        if EXCALIBER_NAME.lower() in [c["name"].lower() for c in clean]:
                            last = last or EXCALIBER_NAME
                        save_instances(clean, last or clean[0]["name"])
                    except Exception:
                        pass
                # keep loader flag in sync with the version string so an
                # imported Forge/Fabric pack never launches as vanilla
                for c in clean:
                    low = str(c.get("version", "")).lower()
                    if "forge" in low or "fabric" in low or "quilt" in low:
                        c["use_forge"] = True
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


def get_game_dir_for_instance(inst: dict | str) -> Path:
    """Resolve the gameDir (saves/mods/config live here) for an instance.

    - Excaliber / any instance with a stored gameDir -> that folder directly
      (so <launcher>/excaliber runs in place, exactly like SKLauncher's
      instances/<name>/ folder - no copy needed).
    - Everything else -> isolated .elysium/instances/<name>/ as before.
    """
    name = inst if isinstance(inst, str) else str(inst.get("name", "default"))
    stored = ""
    if isinstance(inst, dict):
        stored = str(inst.get("gameDir", inst.get("game_dir", ""))).strip()
    if stored:
        try:
            p = Path(stored)
            if not p.is_absolute():
                p = BASE_DIR / stored
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass
    # implicit excaliber mapping even if the JSON entry has no gameDir yet
    if name.strip().lower() == EXCALIBER_NAME.lower() and EXCALIBER_DIR.exists():
        return EXCALIBER_DIR
    return get_instance_dir(name)


def ensure_forge_for_version(version_id: str, java_cmd: str, progress_cb=None) -> str:
    """Install/repair the exact Forge version requested (not just pinned).

    Needed because Excaliber needs 1.20.1-forge-47.4.10 while config pins
    47.2.0. Uses minecraft-launcher-lib directly with the forge_full derived
    from version_id (e.g. '1.20.1-47.4.10').
    """
    import minecraft_launcher_lib as mll

    forge_full = forge_full_from_version(version_id)
    if not forge_full:
        return version_id
    mc_dir = str(get_mc_dir())
    ok, _ = verify_version(version_id)
    if progress_cb:
        progress_cb(40, f"Checking Forge {forge_full}...")
    # already valid -> cheap repair pass (fixes missing libs fast)
    try:
        try:
            mll.forge.install_forge_version(forge_full, mc_dir,
                                            callback=None if progress_cb is None else
                                            _mll_progress_adapter(progress_cb),
                                            java=java_cmd)
        except TypeError:
            # older mll without java kwarg
            mll.forge.install_forge_version(forge_full, mc_dir,
                                            callback=None if progress_cb is None else
                                            _mll_progress_adapter(progress_cb))
    except Exception as e:
        # if it was already valid, a repair failure is non-fatal (launch anyway)
        if ok:
            print(f"[elysium] forge repair skipped ({e})")
            return version_id
        raise RuntimeError(f"Forge {forge_full} install failed: {e}. "
                           f"Start web-proxy (npm start) on school wifi, then retry.")
    ok2, reason2 = verify_version(version_id)
    if not ok2:
        cands = [v for v in list_local_versions() if "forge" in v.lower()]
        if cands:
            # installer sometimes names the version slightly differently
            for c in cands:
                if forge_full.split("-")[-1] in c:
                    return c
            return cands[0]
        raise RuntimeError(f"Forge installed but version json missing: {reason2}")
    return version_id


def _mll_progress_adapter(progress_cb):
    """Adapt simple fn(pct,msg) to minecraft-launcher-lib callback dict."""
    state = {"max": 100, "cur": 0}

    def set_status(s: str):
        try:
            progress_cb(min(99, state["cur"]), str(s))
        except Exception:
            pass

    def set_progress(v: int):
        try:
            state["cur"] = int(v)
            pct = int(v / max(1, state["max"]) * 100)
            progress_cb(min(99, pct), f"Downloading... {pct}%")
        except Exception:
            pass

    def set_max(v: int):
        state["max"] = max(1, int(v))

    return {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}


def launch_with_gamedir(username: str, version_id: str, java_path: str = "",
                        install_forge_flag: bool = False,
                        game_dir: Path | None = None, progress_cb=None,
                        ram: str = DEFAULT_RAM, safe_mode: bool = False):
    """Full install + launch that honours a custom gameDir (SK-style).

    Same as launcher_core.launch but:
    - gameDirectory = game_dir (SK instances/<name>/ keeps its own
      mods/saves/config/resourcepacks/shaderpacks in place)
    - Loader version comes from version_id (47.4.10 works, not just 47.2.0)
    - Shared install dir (.elysium/versions+libraries+assets) still used once
    - RAM is honoured (heavy SK packs OOM-crash without it)
    - Shader crash loop is auto-repaired + title/music packs are restored
    """
    try:
        import minecraft_launcher_lib as mll
    except ImportError:
        raise RuntimeError("Missing dependency: run  pip install -r requirements.txt")

    ensure_dirs()
    mc_dir = str(get_mc_dir())
    if game_dir is None:
        # NEVER launch with the shared install dir as the gameDir - that
        # pollutes .elysium root with mods/config/saves (the old bug).
        game_dir = get_mc_dir() / "instances" / "default"
    if os.path.normcase(str(Path(game_dir))) == os.path.normcase(mc_dir):
        game_dir = get_mc_dir() / "instances" / "default"
    Path(game_dir).mkdir(parents=True, exist_ok=True)
    gdir = Path(game_dir)

    java = find_java(java_path)
    major = check_java_version(java)
    need = java_need_for_version(version_id)
    if major == 0:
        raise RuntimeError(f"Could not verify Java at '{java}'. Install Java {need}+ first.")
    if major and major < need:
        raise RuntimeError(f"Minecraft {version_id} needs Java {need}+ (found Java {major} at {java}).")

    if progress_cb:
        progress_cb(5, f"Using isolated folder {mc_dir}")
        progress_cb(8, f"Game folder {game_dir}")

    # SK parity guards: title/music packs back, shader crash loop repaired.
    # These only touch files when something is actually broken (wiped
    # options.txt / recent ElytraTrims+shader crash), never on a healthy pack.
    try:
        ensure_resourcepacks_enabled(gdir, progress_cb)
    except Exception:
        pass
    try:
        note = ensure_safe_shader_state(gdir, force_safe=safe_mode,
                                        progress_cb=progress_cb)
        if note and progress_cb:
            pass  # note already reported via progress_cb
    except Exception:
        pass

    # base vanilla for this MC line (1.20.1 for all 1.20.1-forge-* targets;
    # same for fabric/quilt ids like 1.20.1-fabric-0.16.9)
    low_v = (version_id or "").lower()
    base_mc = version_id.strip() if version_id.strip() else MC_VERSION
    for sep in ("-forge-", "-fabric-", "-quilt-"):
        if sep in low_v:
            base_mc = version_id[:low_v.index(sep)].strip() or MC_VERSION
            break
    if not base_mc:
        base_mc = MC_VERSION
    if progress_cb:
        progress_cb(10, f"Checking vanilla {base_mc}...")
    install_vanilla(base_mc, progress_cb)

    target = version_id.strip() or MC_VERSION
    low_t = target.lower()
    if install_forge_flag or "forge" in low_t:
        target = ensure_forge_for_version(target, java, progress_cb)
    elif "fabric" in low_t or "quilt" in low_t:
        # best-effort: Fabric/Quilt SK packs install on demand; a clear error
        # beats a cryptic "version not found" crash.
        try:
            target = ensure_fabric_like_for_version(target, java, progress_cb)
        except Exception as e:
            raise RuntimeError(
                f"Loader '{target}' needs manual install first: {e}. "
                f"Tip: launch this folder once from SKLauncher so its "
                f"version json exists, then re-import.")
    elif target != base_mc:
        try:
            if progress_cb:
                progress_cb(60, f"Checking version {target}...")
            install_vanilla(target, progress_cb)
        except Exception:
            target = base_mc

    clean_natives(target)

    if progress_cb:
        progress_cb(85, f"Launching {target}...")

    opts = build_offline_options(username or "Steve")
    opts["launcherName"] = "Elysium"
    opts["launcherVersion"] = "2.0"
    opts["gameDirectory"] = str(game_dir)
    if java_path:
        opts["java"] = java
    if ram in RAM_CHOICES:
        opts["jvmArguments"] = [f"-Xmx{ram}", "-Xms1G"]

    try:
        cmd = mll.command.get_minecraft_command(target, mc_dir, opts)
    except Exception as e:
        raise RuntimeError(f"Could not build launch command for '{target}' ({e}).")
    print("[elysium] " + " ".join(cmd[:6]) + " ...")
    try:
        proc = subprocess.Popen(cmd, cwd=str(game_dir),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise RuntimeError(f"Failed to start Java ({java}): {e}")
    return proc


def ensure_fabric_like_for_version(version_id: str, java_cmd: str,
                                   progress_cb=None) -> str:
    """Best-effort Fabric/Quilt install for imported SK packs."""
    import minecraft_launcher_lib as mll
    mc_dir = str(get_mc_dir())
    ok, _ = verify_version(version_id)
    if ok:
        return version_id
    low = version_id.lower()
    # expected ids: '<mc>-fabric-<loader>' or '<mc>-quilt-<loader>'
    m = re.match(r"^(\d+\.\d+(?:\.\d+)?)-(fabric|quilt)-([0-9A-Za-z.\-_+]+)$",
                 version_id.strip(), re.I)
    if not m:
        return version_id
    mc_ver, loader_kind, loader_ver = m.group(1), m.group(2).lower(), m.group(3)
    if progress_cb:
        progress_cb(40, f"Checking {loader_kind} {loader_ver} for {mc_ver}...")
    try:
        cb = None if progress_cb is None else _mll_progress_adapter(progress_cb)
        if loader_kind == "fabric":
            mll.fabric.install_fabric_version(mc_ver, loader_ver, mc_dir,
                                              callback=cb, java=java_cmd)
        else:
            mll.quilt.install_quilt_version(mc_ver, loader_ver, mc_dir,
                                            callback=cb, java=java_cmd)
    except TypeError:
        try:
            cb = None if progress_cb is None else _mll_progress_adapter(progress_cb)
            if loader_kind == "fabric":
                mll.fabric.install_fabric_version(mc_ver, loader_ver, mc_dir,
                                                  callback=cb)
            else:
                mll.quilt.install_quilt_version(mc_ver, loader_ver, mc_dir,
                                                callback=cb)
        except Exception as e:
            raise RuntimeError(f"{loader_kind} install failed: {e}")
    except Exception as e:
        if ok:
            print(f"[elysium] {loader_kind} repair skipped ({e})")
            return version_id
        raise RuntimeError(f"{loader_kind} {loader_ver} install failed: {e}. "
                           f"Start web-proxy (npm start) on school wifi, then retry.")
    ok2, reason2 = verify_version(version_id)
    if not ok2:
        raise RuntimeError(f"{loader_kind} installed but version json missing: {reason2}")
    return version_id


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
                   progress_cb=None, safe_mode: bool = False):
    """Launch WITHOUT any network verify. Raises if version not ready."""
    try:
        import minecraft_launcher_lib as mll
    except ImportError:
        raise RuntimeError("Missing dependency: run  pip install -r requirements.txt")

    if not is_version_ready(version_id):
        raise RuntimeError(f"Version '{version_id}' is not downloaded yet.")

    mc_dir = str(get_mc_dir())
    if game_dir is None:
        # NEVER fall back to the shared install dir (pollutes .elysium root).
        game_dir = get_mc_dir() / "instances" / "default"
    if os.path.normcase(str(Path(game_dir))) == os.path.normcase(mc_dir):
        game_dir = get_mc_dir() / "instances" / "default"
    Path(game_dir).mkdir(parents=True, exist_ok=True)
    gdir = Path(game_dir)

    java = find_java(java_path)
    major = check_java_version(java)
    need = java_need_for_version(version_id)
    if major == 0:
        raise RuntimeError(f"Could not verify Java at '{java}'. Install Java {need}+ first.")
    if major and major < need:
        raise RuntimeError(f"Minecraft {version_id} needs Java {need}+ (found Java {major} at {java}).")

    # Same SK parity guards as the full path (instant = no downloads, but the
    # pack still needs its title packs + must not boot straight into a known
    # shader crash loop).
    try:
        ensure_resourcepacks_enabled(gdir, progress_cb)
    except Exception:
        pass
    try:
        ensure_safe_shader_state(gdir, force_safe=safe_mode,
                                 progress_cb=progress_cb)
    except Exception:
        pass

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
        raise RuntimeError(f"Could not build launch command for '{version_id}' ({e}).")
    if progress_cb:
        progress_cb(85, f"Starting {version_id}...")
    print("[elysium-instant] " + " ".join(cmd[:6]) + " ...")
    try:
        # cwd = gameDir like SKLauncher (screenshots/logs/crashes land in the
        # pack folder, relative paths inside mods behave identically).
        proc = subprocess.Popen(cmd, cwd=str(game_dir),
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
        self.geometry("640x970")
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
        tk.Label(card, text="INSTANCE", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        self.instance_var = tk.StringVar(value=_cur["name"])
        self.instance_combo = ttk.Combobox(card, textvariable=self.instance_var,
                                           font=("Arial", 11, "bold"), state="readonly")
        self.instance_combo.pack(fill="x", pady=(4, 6), ipady=4)
        self.instance_combo.bind("<<ComboboxSelected>>", self.on_inst_pick)

        ibtns = tk.Frame(card, bg="#FFFEF9")
        ibtns.pack(fill="x", pady=(0, 8))
        tk.Button(ibtns, text="NEW", font=("Arial", 8, "bold"), bg="#F0EDE6", fg="#6B6B6B",
                  relief="flat", padx=10, pady=4, command=self.on_inst_new).pack(side="left", padx=(0, 6))
        tk.Button(ibtns, text="IMPORT", font=("Arial", 8, "bold"), bg="#F0EDE6", fg="#6B6B6B",
                  relief="flat", padx=10, pady=4, command=self.on_inst_import).pack(side="left", padx=(0, 6))
        tk.Button(ibtns, text="SAVE", font=("Arial", 8, "bold"), bg="#F0EDE6", fg="#6B6B6B",
                  relief="flat", padx=10, pady=4, command=self.on_inst_save).pack(side="left", padx=(0, 6))
        tk.Button(ibtns, text="DEL", font=("Arial", 8, "bold"), bg="#F0EDE6", fg="#6B6B6B",
                  relief="flat", padx=10, pady=4, command=self.on_inst_delete).pack(side="left")
        tk.Label(card, text="Drop an SKLauncher instance folder into .elysium/instances/ (or IMPORT) - version, RAM + packs are auto-detected.",
                 font=("Arial", 7), fg=MUTED, bg="#FFFEF9", anchor="w",
                 wraplength=500, justify="left").pack(fill="x", pady=(0, 4))

        # username
        tk.Label(card, text="Username", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        self.name_var = tk.StringVar(value=_cur.get("username", "Steve"))
        tk.Entry(card, textvariable=self.name_var, font=("Arial", 11),
                 bg=ENTRY_BG, relief="flat", highlightbackground="#E7DCC3",
                 highlightthickness=1).pack(fill="x", ipady=7, pady=(4, 10))

        # version header
        head = tk.Frame(card, bg="#FFFEF9")
        head.pack(fill="x")
        tk.Label(head, text="Version", font=("Arial", 9, "bold"),
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
        search.insert(0, "Search")
        search.bind("<FocusIn>", lambda e: search.delete(0, "end") if search.get().strip() == "Search" else None)
        search.bind("<KeyRelease>", lambda e: self.filter_versions())
        search.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        tk.Button(row1, text="REFRESH", font=("Arial", 9, "bold"), bg="#F0EDE6", fg="#8A8A8A",
                  relief="flat", padx=12, pady=6, command=self.on_refresh).pack(side="right")

        self.version_var = tk.StringVar(value=_cur.get("version", f"{MC_VERSION}-forge-{FORGE_VERSION}"))
        self.version_combo = ttk.Combobox(card, textvariable=self.version_var,
                                          font=("Arial", 11), state="readonly")
        self.version_combo.pack(fill="x", pady=(0, 8), ipady=4)
        self.version_combo.bind("<<ComboboxSelected>>", self.on_pick)
        self.all_versions: list[str] = []
        self.on_refresh(silent=True)

        self.forge_var = tk.BooleanVar(value=bool(_cur.get("use_forge", True)))
        tk.Checkbutton(card, text="Install Forge",
                       variable=self.forge_var, font=("Arial", 10),
                       bg="#FFFEF9", fg="#4A4A4A", anchor="w").pack(fill="x", pady=(4, 6))

        # ---- instant + RAM ----
        optrow = tk.Frame(card, bg="#FFFEF9")
        optrow.pack(fill="x", pady=(2, 0))
        self.fast_var = tk.BooleanVar(value=bool(_cur.get("fast", True)))
        tk.Checkbutton(optrow, text="⚡ Instant launch",
                       variable=self.fast_var, font=("Arial", 10, "bold"),
                       bg="#FFFEF9", fg="#2B2B2B", anchor="w").pack(side="left")
        tk.Label(optrow, text="RAM", font=("Arial", 9, "bold"), fg=GOLD_DARK,
                 bg="#FFFEF9").pack(side="left", padx=(10, 4))
        self.ram_var = tk.StringVar(value=_cur.get("ram", DEFAULT_RAM))
        tk.OptionMenu(optrow, self.ram_var, *RAM_CHOICES).pack(side="left")

        saferow = tk.Frame(card, bg="#FFFEF9")
        saferow.pack(fill="x", pady=(4, 0))
        self.safe_var = tk.BooleanVar(value=False)
        tk.Checkbutton(saferow, text="Safe mode OFF = pretty shader title (Image 2); tick ON for no-shader stability (dull title, fixes ElytraTrims/Oculus crash loop)",
                       variable=self.safe_var, font=("Arial", 9),
                       bg="#FFFEF9", fg="#6B6B6B", anchor="w",
                       wraplength=480, justify="left").pack(fill="x")

        tk.Label(card, text="Java", font=("Arial", 9, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9", anchor="w").pack(fill="x")
        row2 = tk.Frame(card, bg="#FFFEF9")
        row2.pack(fill="x", pady=(4, 0))
        _j = _cur.get("java", "") or "Auto"
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
        self.status_var = tk.StringVar(value="Ready.")
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

        # log console - fancy, matches launcher theme
        log_outer = tk.Frame(body, bg=GOLD, padx=1, pady=1)
        log_outer.pack(fill="x", pady=(8, 0))
        log_inner = tk.Frame(log_outer, bg="#FFFEF9")
        log_inner.pack(fill="both", expand=True)
        log_head = tk.Frame(log_inner, bg="#FFFEF9")
        log_head.pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(log_head, text="● ● ●", font=("Arial", 8, "bold"),
                 fg=GOLD, bg="#FFFEF9").pack(side="left")
        tk.Label(log_head, text="CONSOLE", font=("Arial", 8, "bold"),
                 fg=GOLD_DARK, bg="#FFFEF9").pack(side="left", padx=(8, 0))
        tk.Button(log_head, text="CLEAR", font=("Arial", 7, "bold"),
                  bg="#F0EDE6", fg="#8A8A8A", relief="flat", padx=8, pady=2,
                  command=lambda: (self.log.configure(state="normal"),
                                   self.log.delete(1.0, "end"),
                                   self.log.configure(state="disabled"))).pack(side="right")
        self.log = scrolledtext.ScrolledText(log_inner, height=7, font=("Arial", 9),
                                             bg="#FFFEF9", fg="#4A4A4A", relief="flat",
                                             highlightbackground="#E7DCC3", highlightthickness=1,
                                             insertbackground="#4A4A4A",
                                             selectbackground="#E7DCC3")
        self.log.pack(fill="x", padx=10, pady=6)
        self.log.tag_config("ok", foreground="#2E7D32")
        self.log.tag_config("err", foreground="#C62828")
        self.log.tag_config("dim", foreground="#9A958A")
        self.log.insert("end", "Ready.\n")
        self.log.configure(state="disabled")

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
                    get_game_dir_for_instance(inst)
                except Exception:
                    pass
            # warm java detection cache + version list
            try:
                find_java("")
            except Exception:
                pass
            self.after(0, lambda: self._writelog("Ready.\n"))
        except Exception:
            pass

    def _proxy_check(self):
        ok = proxy_reachable()
        self.after(0, lambda: self._writelog(
            f"School proxy {'reachable at ' + CFG.get('proxy_base','')[:40] + '...' if ok else 'NOT running - direct mode (start web-proxy/npm start for blocked Forge hosts)'}\n"))

    def _writelog(self, msg: str):
        low = msg.lower()
        if any(k in low for k in ("fail", "error", "not running", "missing")):
            tag = "err"
        elif any(k in low for k in ("ready", "launch", "⚡", "selected", "saved", "done")):
            tag = "ok"
        else:
            tag = None
        self.log.configure(state="normal")
        text = msg if msg.endswith("\n") else msg + "\n"
        if tag:
            self.log.insert("end", text, tag)
        else:
            self.log.insert("end", text)
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
        self.instance_combo["values"] = [i["name"] for i in self.instances]

    def on_inst_pick(self, _e):
        name = self.instance_var.get().strip()
        if name:
            self._apply_instance(name)

    def _apply_instance(self, name: str, silent=False):
        inst = self._find_instance(name)
        if not inst:
            return
        self.instance_var.set(inst["name"])
        self.name_var.set(inst.get("username", "Steve"))
        self.version_var.set(inst.get("version", MC_VERSION))
        self.java_var.set(inst.get("java", "") or "Auto")
        self.forge_var.set(bool(inst.get("use_forge", True)))
        self.fast_var.set(bool(inst.get("fast", True)))
        rv = inst.get("ram", DEFAULT_RAM)
        self.ram_var.set(rv if rv in RAM_CHOICES else DEFAULT_RAM)
        self._refresh_java_label()
        save_instances(self.instances, inst["name"])
        # Safe mode hint: NEVER pre-tick (pre-ticking forced shaders OFF on
        # every launch, which is what stuck the title on the dull grey
        # no-shader look). Leave it OFF so the pretty shader title boots;
        # the user can tick it manually for stability if the ElytraTrims
        # crash returns.
        try:
            gdir = get_game_dir_for_instance(inst)
            if recent_shader_crashes(gdir):
                if hasattr(self, "safe_var"):
                    self.safe_var.set(False)
                if not silent:
                    self._writelog(
                        f"Note: '{inst['name']}' has recent shader crash(es) - "
                        f"shaders left ON for the pretty title; tick Safe mode "
                        f"only if it crashes again.\n")
        except Exception:
            pass
        if not silent:
            ready = "ready ⚡" if is_version_ready(inst.get("version", "")) else "needs one full install"
            try:
                gdir = get_game_dir_for_instance(inst)
            except Exception:
                gdir = "?"
            self._writelog(f"Instance '{inst['name']}' selected ({inst.get('version','?')}, {ready}) -> {gdir}\n")
            self.status_var.set(f"{inst['name']}: {inst.get('version','?')} ({ready})")

    def _collect_to_instance(self, inst: dict):
        inst["username"] = self.name_var.get().strip() or "Steve"
        inst["version"] = self.version_var.get().strip() or MC_VERSION
        j = self.java_var.get().strip()
        inst["java"] = "" if j.startswith("Auto") else j
        inst["use_forge"] = bool(self.forge_var.get())
        inst["ram"] = self.ram_var.get() if self.ram_var.get() in RAM_CHOICES else DEFAULT_RAM
        inst["fast"] = bool(self.fast_var.get())
        # never drop the custom gameDir of an imported SK pack (Excaliber in
        # <launcher>/excaliber, or any folder in .elysium/instances/<Name>/).
        if inst.get("gameDir"):
            return  # imported pack already points at its folder - keep it
        if inst.get("name", "").strip().lower() == EXCALIBER_NAME.lower():
            inst["gameDir"] = str(EXCALIBER_DIR)
        # normal instances stay isolated, no key needed

    def on_inst_new(self):
        name = simpledialog.askstring("New instance", "Instance name:",
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
            get_game_dir_for_instance(inst)
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

    def on_inst_import(self):
        """IMPORT an SKLauncher instance folder (mods/saves/config/...).

        Pick the SK folder -> it is copied into .elysium/instances/<Name>/
        (in place when already there) and becomes a normal instance with
        auto-detected version + RAM + username, so it launches like SK.
        """
        src = filedialog.askdirectory(
            title="Pick SKLauncher instance folder (the one with mods/)",
            mustexist=True)
        if not src:
            return
        try:
            src_p = Path(src)
            if not is_gamedir_folder(src_p):
                messagebox.showwarning(
                    "Not a game folder",
                    f"'{src_p.name}' has no mods/config/saves/resourcepacks.\n"
                    f"Pick the instance folder itself (the one with mods/ inside).")
                return
            ensure_dirs()
            dest_root = get_mc_dir() / "instances"
            dest_root.mkdir(parents=True, exist_ok=True)
            # already inside instances (or is excaliber)? use in place.
            try:
                inside = os.path.normcase(str(src_p.resolve(strict=False))).startswith(
                    os.path.normcase(str(dest_root.resolve(strict=False))))
            except Exception:
                inside = False
            if src_p == EXCALIBER_DIR or inside:
                dest = src_p
                copied = False
            else:
                base = folder_name_to_instance_name(src_p)
                safe = _safe_instance_name(base)
                dest = dest_root / safe
                n = 2
                while dest.exists():
                    dest = dest_root / f"{safe}_{n}"
                    n += 1
                self._writelog(f"Copying SK folder '{src_p.name}' -> {dest} ...\n")
                self.status_var.set(f"Importing {src_p.name}...")
                self.update_idletasks()
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns(
                    "logs", ".mixin.out", "__pycache__"))
                copied = True
            ver = detect_version_for_gamedir(
                dest, self.version_var.get().strip() or f"{MC_VERSION}-forge-{FORGE_VERSION}")
            name = folder_name_to_instance_name(dest)
            base = name
            n = 2
            while self._find_instance(name):
                name = f"{base} {n}"[:32]
                n += 1
            low = ver.lower()
            inst = {
                "name": name, "version": ver,
                "username": detect_username_for_gamedir(dest) or self.name_var.get().strip() or "Steve",
                "java": "", "use_forge": bool("forge" in low or "fabric" in low or "quilt" in low),
                "ram": suggest_ram_for_gamedir(dest),
                "fast": True, "gameDir": str(dest)}
            self.instances.insert(0, inst)
            save_instances(self.instances, name)
            self.refresh_inst_listbox()
            self._apply_instance(name)
            # keep the detected version selectable even before first download
            try:
                self.on_refresh(silent=True)
                if ver and ver not in (self.version_combo["values"] or []):
                    self.version_combo["values"] = [ver] + list(self.version_combo["values"])
                self.version_var.set(ver)
            except Exception:
                pass
            self._writelog(
                f"Imported SK folder as '{name}' ({ver}, {inst['ram']}) -> {dest} "
                f"({'copied' if copied else 'in place'}).\n")
            self.status_var.set(f"Imported '{name}' ({ver})")
        except Exception as ex:
            messagebox.showerror("Import failed", str(ex))

    def on_inst_delete(self):
        name = self.instance_var.get().strip()
        if len(self.instances) <= 1:
            messagebox.showwarning("Keep one", "You need at least one instance.")
            return
        if not messagebox.askyesno("Delete?", f"Delete instance '{name}'?"):
            return
        self.instances = [i for i in self.instances if i["name"] != name]
        save_instances(self.instances, self.instances[0]["name"])
        self.refresh_inst_listbox()
        self._apply_instance(self.instances[0]["name"])

    # ----- versions -----
    def refresh_listbox(self, items):
        self.version_combo["values"] = list(items)
        self.count_lbl.config(text=f"{len(items)} of {len(self.all_versions)}")

    def filter_versions(self):
        q = self.search_var.get().lower().strip()
        if q == "search":
            q = ""
        if not q:
            self.refresh_listbox(self.all_versions)
        else:
            self.refresh_listbox([v for v in self.all_versions if q in v.lower()])

    def on_pick(self, _e):
        raw = self.version_var.get().strip()
        if raw and "forge" in raw.lower():
            self.forge_var.set(True)

    def on_refresh(self, silent=False):
        try:
            ensure_dirs()
            # picking up freshly dropped SK folders is part of refresh, so a
            # copied instances/<Name>/ folder shows up without restart
            try:
                if auto_import_missing(self.instances):
                    save_instances(self.instances,
                                   self.instance_var.get().strip() or self.instances[0]["name"])
                    self.refresh_inst_listbox()
                    if not silent:
                        self._writelog("Auto-imported new folder(s) from .elysium/instances/.\n")
            except Exception:
                pass
            self.all_versions = list_local_versions()
            cur = self.version_var.get().strip() if hasattr(self, "version_var") else ""
            # Keep SK-imported versions (e.g. Excaliber's 47.4.10) selectable
            # even before first download: show them on top instead of resetting.
            items = list(self.all_versions)
            if cur and cur not in items:
                items = [cur] + items
            self.refresh_listbox(items)
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
            inst = self._find_instance(self.instance_var.get().strip() or "default")
            d = get_game_dir_for_instance(inst if inst else (self.instance_var.get().strip() or "default"))
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
        safe_mode = bool(self.safe_var.get()) if hasattr(self, "safe_var") else False
        low_ver = ver.lower()
        do_forge = self.forge_var.get() or "forge" in low_ver or "fabric" in low_ver or "quilt" in low_ver

        # SK-style gameDir: imported packs run in their own folder (their own
        # mods/saves/config), others run in .elysium/instances/<name>/.
        gdir = get_game_dir_for_instance(inst)

        # INSTANT PATH: no downloads, straight to game
        if want_fast and is_version_ready(ver):
            self.set_progress(30, f"⚡ Instant launch '{inst_name}' ({ver}) - no waiting...")
            def fast_worker():
                try:
                    proc = instant_launch(user, ver, java, ram, gdir,
                                          progress_cb=lambda p, m: self.after(0, lambda: self.set_progress(p, m)),
                                          safe_mode=safe_mode)
                    self.after(0, lambda: (self.set_progress(100, f"Launched {ver} as {user} (pid {proc.pid})"),
                                            self._writelog(f"⚡ Instant: '{inst_name}' -> {gdir}\n")))
                except Exception as ex:
                    msg = self._friendly_error(ex)
                    self.after(0, lambda: (self.set_progress(0, "Instant failed - see popup"),
                                            messagebox.showerror("Instant launch failed", msg)))
            threading.Thread(target=fast_worker, daemon=True).start()
            return

        # FULL PATH (gameDir-aware + exact loader version, so Excaliber's
        # 1.20.1-forge-47.4.10 installs correctly instead of pinned 47.2.0)
        self.set_progress(2, f"Installing {ver}...")
        def worker():
            try:
                proc = launch_with_gamedir(user, ver, java, do_forge, gdir,
                              progress_cb=lambda p, m: self.after(0, lambda: self.set_progress(p, m)),
                              ram=ram, safe_mode=safe_mode)
                self.after(0, lambda: (self.set_progress(100, f"Launched {ver} as {user} (pid {proc.pid})"),
                                        self.on_refresh(silent=True)))
            except Exception as ex:
                msg = self._friendly_error(ex)
                self.after(0, lambda: (self.set_progress(0, "Launch failed - see popup"),
                                        messagebox.showerror("Launch failed", msg)))
        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    request_password()
    Elysium().mainloop()
