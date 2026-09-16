"""
Elysium launcher core v2 - isolated, school-wifi friendly, self-repairing.

Design goals:
- NEVER touch the official .minecraft folder. Uses its own %APPDATA%/.elysium
  folder from scratch (like SKLauncher's isolated instance concept).
- School wifi friendly: try direct Mojang/Forge first, fall back to your
  local Node proxy (web-proxy/server.js) with retries.
- Self-repair: every launch verifies vanilla + forge files and re-downloads
  only missing/corrupt files. Fixes the classic
  "org.lwjgl / jna / jopt-simple was not found" errors caused by a
  half-deleted .minecraft folder.
- You must own Minecraft: Java Edition. Offline username mode does NOT grant
  ownership - game files are downloaded from Mojang's official CDN.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / ".elysium_auth.json"  # reserved for future MS login

DEFAULT_CONFIG = {
    # Portable by default: .elysium next to the launcher (avoids Microsoft-Store
    # Python file-virtualization which hides %APPDATA% folders + avoids
    # touching official .minecraft). Set to "%APPDATA%/.elysium" in config.json
    # only if you use python.org Python.
    "minecraft_dir": "./.elysium",
    "mc_version": "1.20.1",
    "forge_version": "47.2.0",
    "forge_full": "1.20.1-47.2.0",
    "java_min_major": 17,
    "proxy_base": "http://localhost:3000/proxy?url=",
    "use_proxy_for_forge": True,
    "use_proxy_fallback": True,
    "max_retries": 3,
}

FORGE_MAVEN_BASE = "https://maven.minecraftforge.net"
FORGE_PROMOTIONS_URL = (
    "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
)


def _expand(p: str) -> str:
    # NOTE: do NOT use Path.resolve() here - Microsoft Store Python redirects
    # %APPDATA% writes to LocalCache\Roaming, making files invisible to Java /
    # Explorer and causing "library was not found" errors. abspath() keeps the
    # real path string without hitting the virtualized FS.
    import os.path as _osp
    s = os.path.expandvars(os.path.expanduser(p))
    if not _osp.isabs(s):
        s = _osp.join(str(BASE_DIR), s)
    return _osp.abspath(s)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            # Migrate old shared folders to isolated portable one
            old = str(user.get("minecraft_dir", ""))
            if ".minecraft-elysium" in old or old.rstrip("/\\").endswith(".minecraft"):
                user["minecraft_dir"] = DEFAULT_CONFIG["minecraft_dir"]
            # Microsoft Store Python virtualizes %APPDATA% -> invisible to Java.
            # Force portable in that case (this was the "library not found" cause).
            if "%APPDATA%" in old.upper() and "PythonSoftwareFoundation" in sys.executable:
                user["minecraft_dir"] = DEFAULT_CONFIG["minecraft_dir"]
            cfg.update(user)
        except Exception as e:
            print(f"[elysium] config load failed ({e}), using defaults")
    # fill derived
    cfg["minecraft_dir_resolved"] = _expand(cfg.get("minecraft_dir", DEFAULT_CONFIG["minecraft_dir"]))
    # keep forge_full in sync if user only changed pieces
    mc = cfg.get("mc_version", "1.20.1")
    fv = cfg.get("forge_version", "47.2.0")
    if "forge_full" not in cfg or not cfg["forge_full"].startswith(mc):
        cfg["forge_full"] = f"{mc}-{fv}"
    return cfg


def save_config(cfg: dict) -> None:
    out = {k: v for k, v in cfg.items() if not k.endswith("_resolved")}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


CFG = load_config()

MC_VERSION = CFG.get("mc_version", "1.20.1")
FORGE_VERSION = CFG.get("forge_version", "47.2.0")
FORGE_FULL = CFG.get("forge_full", f"{MC_VERSION}-{FORGE_VERSION}")
INSTALLER_URL = (
    f"{FORGE_MAVEN_BASE}/net/minecraftforge/forge/{FORGE_FULL}/forge-{FORGE_FULL}-installer.jar"
)


def get_mc_dir() -> Path:
    return Path(CFG["minecraft_dir_resolved"])


def ensure_dirs() -> Path:
    mc = get_mc_dir()
    mc.mkdir(parents=True, exist_ok=True)
    for sub in ("versions", "libraries", "assets", "forge-installer", "logs"):
        (mc / sub).mkdir(parents=True, exist_ok=True)
    return mc


# ---------- proxy / networking ----------

def via_proxy(url: str) -> str:
    base = CFG.get("proxy_base", DEFAULT_CONFIG["proxy_base"])
    return base + quote(url, safe="")


def proxy_reachable(timeout: float = 2.5) -> bool:
    try:
        base = CFG.get("proxy_base", "")
        # hit /health on same host:port
        m = re.match(r"(https?://[^/]+)", base)
        if not m:
            return False
        health = m.group(1) + "/health"
        req = Request(health, headers={"User-Agent": "Elysium-Launcher/2.0"})
        with urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def http_get(url: str, timeout: int = 30) -> bytes:
    """Direct GET with browser UA."""
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Elysium-Launcher/2.0",
        "Accept": "*/*",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def http_get_school_friendly(url: str, timeout: int = 30) -> bytes:
    """
    School-wifi friendly GET:
    1. try direct (Mojang is usually open)
    2. on failure, try via local proxy (for Forge maven/files which schools block)
    """
    last_err: Exception | None = None
    tries = int(CFG.get("max_retries", 3))
    # attempt 1..n direct with small backoff
    for i in range(max(1, tries)):
        try:
            return http_get(url, timeout=timeout)
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (i + 1))
    # fallback to proxy
    if CFG.get("use_proxy_fallback", True) or CFG.get("use_proxy_for_forge", True):
        try:
            return http_get(via_proxy(url), timeout=timeout + 30)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Download failed (direct + proxy) for {url[:100]}: {last_err}")


def get_forge_promotions():
    try:
        raw = http_get_school_friendly(FORGE_PROMOTIONS_URL, timeout=20)
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"[elysium] promotions fetch failed ({e}), using pinned {FORGE_VERSION}")
        return {"promos": {f"{MC_VERSION}-recommended": FORGE_VERSION}}


# ---------- java ----------

def find_java(custom: str = "") -> str:
    if custom and Path(custom).exists():
        return custom
    # prefer javaw for GUI launch (no console popup), then java
    for cand in ("javaw.exe", "java.exe", "java"):
        try:
            out = subprocess.run([cand, "-version"], capture_output=True,
                                 text=True, timeout=6)
            if out.returncode == 0:
                return cand
        except Exception:
            continue
    # last resort: JAVA_HOME
    jh = os.environ.get("JAVA_HOME", "")
    if jh:
        for name in ("bin\\javaw.exe", "bin\\java.exe"):
            p = Path(jh) / name
            if p.exists():
                return str(p)
    return "java"


def check_java_version(java_cmd: str) -> int:
    try:
        p = subprocess.run([java_cmd, "-version"], capture_output=True,
                           text=True, timeout=6)
        txt = (p.stderr or "") + (p.stdout or "")
        m = re.search(r'version "(\d+)(?:\.(\d+))?', txt)
        if m:
            major = int(m.group(1))
            # old scheme: 1.8 -> 8
            if major == 1 and m.group(2):
                return int(m.group(2))
            return major
    except Exception:
        pass
    return 0


def java_info(java_cmd: str) -> str:
    major = check_java_version(java_cmd)
    if major:
        return f"{java_cmd} (Java {major})"
    return f"{java_cmd} (version unknown)"


# ---------- install / verify ----------

def _mll_callback(progress_cb):
    """Adapt simple fn(pct,msg) to minecraft-launcher-lib callback dict."""
    state = {"max": 100, "cur": 0}
    if progress_cb is None:
        return None

    def set_status(s: str):
        try:
            progress_cb(min(99, state["cur"]), str(s))
        except Exception:
            pass

    def set_progress(v: int):
        try:
            state["cur"] = int(v)
            mx = max(1, state["max"])
            pct = int(v / mx * 100)
            progress_cb(min(99, pct), f"Downloading... {pct}%")
        except Exception:
            pass

    def set_max(v: int):
        state["max"] = max(1, int(v))

    return {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}


def version_json_path(version_id: str) -> Path:
    return get_mc_dir() / "versions" / version_id / f"{version_id}.json"


def verify_version(version_id: str) -> tuple[bool, str]:
    """Check version json exists and parses. Returns (ok, reason)."""
    p = version_json_path(version_id)
    if not p.exists():
        return False, f"missing {p.name}"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if "libraries" not in data and "mainClass" not in data:
            return False, "version json incomplete"
        return True, "ok"
    except Exception as e:
        return False, f"corrupt version json ({e})"


def clean_natives(version_id: str) -> None:
    """Delete extracted natives - fixes LWJGL/JNA UnsatisfiedLinkError."""
    for suffix in ("-natives", "-natives-windows", ""):
        d = get_mc_dir() / "versions" / version_id / f"{version_id}{suffix}" if suffix else None
        # mll extracts to a temp natives dir; also clean common pattern:
    ndir = get_mc_dir() / "versions" / version_id / "natives"
    if ndir.exists():
        shutil.rmtree(ndir, ignore_errors=True)
    # also any folder ending with -natives under versions/version_id
    vdir = get_mc_dir() / "versions" / version_id
    if vdir.exists():
        for child in vdir.iterdir():
            if child.is_dir() and "native" in child.name.lower():
                shutil.rmtree(child, ignore_errors=True)


def _nuke_broken_version(version_id: str) -> None:
    d = get_mc_dir() / "versions" / version_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def install_vanilla(version: str, progress_cb=None) -> None:
    import minecraft_launcher_lib as mll
    ok, reason = verify_version(version)
    # mll.install repairs automatically, so always call it; but if json is
    # corrupt, nuke first so it re-downloads cleanly (fixes your jna/lwjgl bug)
    if not ok and reason.startswith("corrupt"):
        _nuke_broken_version(version)
    mll.install.install_minecraft_version(version, str(get_mc_dir()),
                                          callback=_mll_callback(progress_cb))
    ok2, reason2 = verify_version(version)
    if not ok2:
        raise RuntimeError(f"Vanilla {version} install incomplete: {reason2}. "
                           f"Check internet / proxy, then hit Repair.")


def download_installer(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[elysium] downloading Forge installer (school-friendly):\n  {INSTALLER_URL}")
    data = http_get_school_friendly(INSTALLER_URL, timeout=120)
    if len(data) < 1_000_000:
        raise RuntimeError(f"Forge installer suspiciously small ({len(data)} bytes) - blocked?")
    dest.write_bytes(data)
    print(f"[elysium] saved {len(data)} bytes -> {dest}")
    return dest


def install_forge(forge_full: str, java_cmd: str, progress_cb=None) -> str:
    """
    Install Forge, return resulting version id (e.g. 1.20.1-forge-47.2.0).
    Strategy: direct mll install first, proxy-jar fallback second.
    """
    import minecraft_launcher_lib as mll
    forge_id = f"{MC_VERSION}-forge-{FORGE_VERSION}"

    # already installed + valid? skip heavy reinstall
    ok, _ = verify_version(forge_id)
    if ok:
        if progress_cb:
            progress_cb(55, f"Forge {forge_full} already installed - verifying")
        # still run mll install to repair missing libs (cheap if complete)
        try:
            mll.forge.install_forge_version(forge_full, str(get_mc_dir()),
                                            callback=_mll_callback(progress_cb),
                                            java=java_cmd)
        except TypeError:
            # older lib without java kwarg
            mll.forge.install_forge_version(forge_full, str(get_mc_dir()),
                                            callback=_mll_callback(progress_cb))
        return forge_id

    if progress_cb:
        progress_cb(30, f"Installing Forge {forge_full}...")
    try:
        try:
            mll.forge.install_forge_version(forge_full, str(get_mc_dir()),
                                            callback=_mll_callback(progress_cb),
                                            java=java_cmd)
        except TypeError:
            mll.forge.install_forge_version(forge_full, str(get_mc_dir()),
                                            callback=_mll_callback(progress_cb))
        return forge_id
    except Exception as e:
        print(f"[elysium] direct forge install failed ({e}), retrying via proxy jar")
        if progress_cb:
            progress_cb(40, "Direct Forge blocked - retrying via school proxy...")
        jar = download_installer(get_mc_dir() / "forge-installer" / f"forge-{forge_full}-installer.jar")
        # installer needs java 17+ and takes a while
        r = subprocess.run([java_cmd, "-jar", str(jar), "--installClient", str(get_mc_dir())],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"Forge installer jar failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        ok2, reason2 = verify_version(forge_id)
        if not ok2:
            # some installer builds name the version slightly differently - pick it up
            cands = [v for v in list_local_versions() if "forge" in v.lower()]
            if cands:
                return cands[0]
            raise RuntimeError(f"Forge installed but version json missing: {reason2}")
        return forge_id


def build_offline_options(username: str) -> dict:
    import hashlib
    import uuid as uuidlib
    h = hashlib.md5(f"OfflinePlayer:{username}".encode()).digest()
    b = bytearray(h)
    b[6] = (b[6] & 0x0F) | 0x30
    b[8] = (b[8] & 0x3F) | 0x80
    return {
        "username": username,
        "uuid": str(uuidlib.UUID(bytes=bytes(b))),
        "token": "0",
    }


def launch(username: str, version_id: str, java_path: str = "",
           install_forge_flag: bool = False, progress_cb=None):
    """
    Full flawless flow:
    1. isolated .elysium dir
    2. java check (>=17)
    3. install/repair vanilla
    4. optionally install forge
    5. clean natives (fixes lwjgl/jna errors)
    6. build command + detached launch
    """
    try:
        import minecraft_launcher_lib as mll
    except ImportError:
        raise RuntimeError("Missing dependency: run  pip install -r requirements.txt")

    ensure_dirs()
    mc_dir = str(get_mc_dir())
    java = find_java(java_path)
    major = check_java_version(java)
    need = int(CFG.get("java_min_major", 17))
    if major and major < need:
        raise RuntimeError(f"Minecraft {MC_VERSION} needs Java {need}+ (found Java {major} at {java}). "
                           f"Install Temurin 17/21 and point JAVA EXECUTABLE at it.")
    if major == 0:
        raise RuntimeError(f"Could not verify Java at '{java}'. Install Java {need}+ first.")

    if progress_cb:
        progress_cb(5, f"Using isolated folder {mc_dir}")
        progress_cb(8, f"Java OK: {java_info(java)}")

    # 1. vanilla (always verify - this is what fixes deleted-folder corruption)
    if progress_cb:
        progress_cb(10, f"Checking vanilla {MC_VERSION}...")
    install_vanilla(MC_VERSION, progress_cb)

    forge_id = f"{MC_VERSION}-forge-{FORGE_VERSION}"
    target = version_id.strip() or MC_VERSION

    # 2. forge?
    if install_forge_flag or "forge" in target.lower():
        target = install_forge(FORGE_FULL, java, progress_cb)
    else:
        # user picked plain version that isn't downloaded yet? install it
        if target != MC_VERSION:
            try:
                if progress_cb:
                    progress_cb(60, f"Checking version {target}...")
                install_vanilla(target, progress_cb)
            except Exception:
                # unknown custom version string - fall back to vanilla
                target = MC_VERSION

    # 3. clean stale natives (the "xxx.dll / jna / lwjgl not found" fix)
    clean_natives(target)
    clean_natives(forge_id)

    if progress_cb:
        progress_cb(85, f"Launching {target}...")

    opts = build_offline_options(username or "Steve")
    opts["launcherName"] = "Elysium"
    opts["launcherVersion"] = "2.0"
    if java_path:
        opts["java"] = java

    try:
        cmd = mll.command.get_minecraft_command(target, mc_dir, opts)
    except Exception as e:
        raise RuntimeError(
            f"Could not build launch command for '{target}' ({e}).\n"
            f"Hit REPAIR to re-download libraries, then try again.\n"
            f"Isolated folder: {mc_dir}")

    print("[elysium] " + " ".join(cmd[:6]) + " ...")
    try:
        proc = subprocess.Popen(cmd, cwd=mc_dir,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise RuntimeError(f"Failed to start Java ({java}): {e}")
    return proc


def list_local_versions() -> list[str]:
    mc_dir = get_mc_dir()
    vers: list[str] = []
    vdir = mc_dir / "versions"
    if vdir.exists():
        for p in sorted(vdir.iterdir()):
            if p.is_dir() and (p / f"{p.name}.json").exists():
                vers.append(p.name)
    defaults = [f"{MC_VERSION}-forge-{FORGE_VERSION}", MC_VERSION]
    out: list[str] = []
    for d in defaults:
        if d not in vers:
            out.append(d)
    out.extend(sorted(vers))
    # de-dupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for v in out:
        if v not in seen:
            uniq.append(v)
            seen.add(v)
    return uniq


def repair_all(progress_cb=None) -> str:
    """Force full verify: nuke natives, reinstall vanilla + forge metadata."""
    ensure_dirs()
    java = find_java("")
    if progress_cb:
        progress_cb(5, "Repair: cleaning natives...")
    for v in list(list_local_versions()):
        clean_natives(v)
    if progress_cb:
        progress_cb(20, f"Repair: re-verifying vanilla {MC_VERSION}...")
    install_vanilla(MC_VERSION, progress_cb)
    if progress_cb:
        progress_cb(90, "Repair complete")
    return str(get_mc_dir())


if __name__ == "__main__":
    print("MC:", MC_VERSION, "Forge:", FORGE_FULL)
    print("Isolated dir:", get_mc_dir())
    print("Java:", java_info(find_java("")))
    print("Proxy reachable:", proxy_reachable())
    print("Installer (direct):", INSTALLER_URL)
    print("Installer (proxied):", via_proxy(INSTALLER_URL))
    print("Local versions:", list_local_versions())
