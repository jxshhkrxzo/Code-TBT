import json
import os
import shutil
import subprocess
import threading
import uuid
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox
import minecraft_launcher_lib

# Configure the global theme configuration immediately
ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

PLACEHOLDER_LOADING = "Fetching entries..."
PLACEHOLDER_EMPTY = "No matching versions"


class ModernLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("Elysium")
        self.root.geometry("580x740")
        self.root.resizable(False, False)
        self.root.configure(fg_color="#faf8f5")  # Ivory White Base

        # Color Palette Definition Constants
        self.GOLD_MAIN = "#dcae5b"
        self.GOLD_DEEP = "#aa8232"
        self.TEXT_MAIN = "#1c1812"

        self.minecraft_directory = minecraft_launcher_lib.utils.get_minecraft_directory()
        self._progress_max = 1
        self.all_versions = []      # master list; the combo box shows a filtered view
        self.local_versions = set()  # ids found on disk rather than in Mojang's manifest

        # --- UI LAYOUT MATRIX ---
        # 1. Main Decorative Canvas for Gold Framing Lines
        self.canvas = tk.Canvas(root, bg="#faf8f5", bd=0, highlightthickness=0)
        self.canvas.place(x=0, y=0, width=580, height=740)
        self.draw_royal_frame()

        # 2. Premium Bold Header
        self.title_label = ctk.CTkLabel(root, text="ELYSIUM", font=("Times New Roman", 46, "bold", "italic"), text_color=self.TEXT_MAIN, bg_color="#faf8f5", width=500)
        self.title_label.place(x=40, y=50)

        self.subtitle = ctk.CTkLabel(root, text="P Y T H O N   E D I T I O N", font=("Arial", 10, "bold"), text_color="#7a7160", bg_color="#faf8f5", width=500)
        self.subtitle.place(x=40, y=115)

        # 3. Interactive Component Container Card
        self.card = ctk.CTkFrame(root, fg_color="#ffffff", corner_radius=12, border_width=2, border_color="#f5e2b3", width=450, height=400)
        self.card.place(x=65, y=180)

        # Username Configuration Field
        self.user_label = ctk.CTkLabel(self.card, text="PROFILER HANDLE", font=("Times New Roman", 12, "bold"), text_color=self.GOLD_DEEP)
        self.user_label.place(x=35, y=20)
        self.username_entry = ctk.CTkEntry(self.card, fg_color="#f4f1ea", text_color=self.TEXT_MAIN, font=("Arial", 12, "bold"), border_color="#ebdcc1", corner_radius=6, width=380, height=40)
        self.username_entry.place(x=35, y=48)
        self.username_entry.insert(0, "Steve")

        # Version Section: search filter + import + dropdown
        self.version_label = ctk.CTkLabel(self.card, text="SYSTEM RUNTIME VERSION", font=("Times New Roman", 12, "bold"), text_color=self.GOLD_DEEP)
        self.version_label.place(x=35, y=108)

        self.match_label = ctk.CTkLabel(self.card, text="", font=("Arial", 10), text_color="#7a7160", anchor="e", width=120)
        self.match_label.place(x=295, y=109)

        self.search_entry = ctk.CTkEntry(
            self.card,
            placeholder_text="Search  (e.g. 1.20,  forge,  snapshot)",
            fg_color="#f4f1ea",
            text_color=self.TEXT_MAIN,
            placeholder_text_color="#a79c86",
            font=("Arial", 11),
            border_color="#ebdcc1",
            corner_radius=6,
            width=290,
            height=34,
        )
        self.search_entry.place(x=35, y=136)
        self.search_entry.bind("<KeyRelease>", self.on_search)
        self.search_entry.bind("<Escape>", self.clear_search)

        self.import_btn = ctk.CTkButton(
            self.card,
            text="IMPORT",
            font=("Arial", 11, "bold"),
            fg_color="#f4f1ea",
            hover_color="#ebdcc1",
            text_color=self.GOLD_DEEP,
            border_width=1,
            border_color="#ebdcc1",
            corner_radius=6,
            width=82,
            height=34,
            command=self.start_import,
        )
        self.import_btn.place(x=333, y=136)

        self.version_combo = ctk.CTkComboBox(self.card, fg_color="#f4f1ea", text_color=self.TEXT_MAIN, border_color="#ebdcc1", button_color=self.GOLD_MAIN, corner_radius=6, values=[PLACEHOLDER_LOADING], width=380, height=40)
        self.version_combo.place(x=35, y=178)

        # Forge System Framework Toggle Switch
        self.forge_var = ctk.BooleanVar()
        self.forge_check = ctk.CTkCheckBox(
            self.card,
            text="Initialize Matrix Framework (Forge System)",
            variable=self.forge_var,
            font=("Arial", 11, "bold"),
            text_color=self.TEXT_MAIN,
            fg_color=self.GOLD_MAIN,
            border_color="#ebdcc1",
            width=380
        )
        self.forge_check.place(x=35, y=248)

        self.forge_hint = ctk.CTkLabel(self.card, text="Leave off when launching an already-imported Forge version.", font=("Arial", 9), text_color="#a79c86", anchor="w", width=380)
        self.forge_hint.place(x=59, y=278)

        # Java Executable Override - pins the launch to one specific java.exe
        # instead of trusting a bare "java" PATH lookup, which is what was
        # crashing with the jli.dll error (it was resolving to the wrong
        # or an incomplete Java install on that machine).
        self.java_label = ctk.CTkLabel(self.card, text="JAVA EXECUTABLE (optional)", font=("Times New Roman", 12, "bold"), text_color=self.GOLD_DEEP)
        self.java_label.place(x=35, y=305)
        self.java_entry = ctk.CTkEntry(
            self.card,
            placeholder_text="Auto (bundled runtime / PATH)",
            fg_color="#f4f1ea",
            text_color=self.TEXT_MAIN,
            placeholder_text_color="#a79c86",
            font=("Arial", 11),
            border_color="#ebdcc1",
            corner_radius=6,
            width=290,
            height=34,
        )
        self.java_entry.place(x=35, y=333)
        self.java_browse_btn = ctk.CTkButton(
            self.card,
            text="BROWSE",
            font=("Arial", 11, "bold"),
            fg_color="#f4f1ea",
            hover_color="#ebdcc1",
            text_color=self.GOLD_DEEP,
            border_width=1,
            border_color="#ebdcc1",
            corner_radius=6,
            width=82,
            height=34,
            command=self.browse_java,
        )
        self.java_browse_btn.place(x=333, y=333)

        # 4. Status Communication Line
        self.status_label = ctk.CTkLabel(root, text="Synchronizing luxurious layout arrays...", font=("Times New Roman", 13, "bold", "italic"), text_color=self.TEXT_MAIN, bg_color="#faf8f5", width=500)
        self.status_label.place(x=40, y=600)

        self.progress = ctk.CTkProgressBar(root, progress_color=self.GOLD_MAIN, fg_color="#ebdcc1", height=6, width=450)
        self.progress.place(x=65, y=633)
        self.progress.set(0.0)

        # 5. Heavy Execution Action Button
        self.launch_btn = ctk.CTkButton(root, text="LAUNCH SYSTEM", font=("Times New Roman", 16, "bold"), fg_color=self.GOLD_MAIN, hover_color="#ebd19b", text_color="#ffffff", corner_radius=8, command=self.start_launch_thread, width=290, height=52)
        self.launch_btn.place(x=145, y=655)

        # Offload Mojang version fetching to a separate worker thread to avoid startup lockouts
        threading.Thread(target=self.load_all_versions, daemon=True).start()

    def draw_royal_frame(self):
        """Draws clean geometric white-gold border layers via canvas backing."""
        self.canvas.create_rectangle(16, 16, 564, 724, outline=self.GOLD_DEEP, width=2)
        self.canvas.create_rectangle(22, 22, 558, 718, outline=self.GOLD_MAIN, width=1.5)

    # --- Thread-safe UI helpers -------------------------------------------
    def set_status(self, text):
        self.root.after(0, lambda: self.status_label.configure(text=text))

    def set_max(self, value):
        self._progress_max = value if value else 1

    def set_progress(self, value):
        fraction = min(value / self._progress_max, 1.0)
        self.root.after(0, lambda: self.progress.set(fraction))

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        launch_color = "#ebdcc1" if busy else self.GOLD_MAIN
        self.root.after(0, lambda: self.launch_btn.configure(state=state, fg_color=launch_color))
        self.root.after(0, lambda: self.import_btn.configure(state=state))

    def build_callback(self):
        """Progress hooks handed to minecraft_launcher_lib's installers."""
        return {
            "setStatus": self.set_status,
            "setProgress": self.set_progress,
            "setMax": self.set_max,
        }

    # --- Version searching / filtering -------------------------------------
    def on_search(self, event=None):
        self.apply_filter()

    def clear_search(self, event=None):
        self.search_entry.delete(0, "end")
        self.apply_filter()

    def apply_filter(self):
        """Narrow the dropdown to versions containing every whitespace-separated term."""
        terms = self.search_entry.get().lower().split()
        if terms:
            matches = [v for v in self.all_versions if all(t in v.lower() for t in terms)]
        else:
            matches = list(self.all_versions)

        current = self.version_combo.get()
        self.version_combo.configure(values=matches or [PLACEHOLDER_EMPTY])

        # Only overwrite the visible selection when it fell out of the result set,
        # so an already-chosen version survives incidental typing.
        if current not in matches:
            self.version_combo.set(matches[0] if matches else PLACEHOLDER_EMPTY)

        if not self.all_versions:
            self.match_label.configure(text="")
        elif matches:
            self.match_label.configure(text=f"{len(matches)} of {len(self.all_versions)}")
        else:
            self.match_label.configure(text="no matches")

    def browse_java(self):
        path = filedialog.askopenfilename(
            title="Select java.exe or javaw.exe",
            filetypes=[("Java executable", "java.exe javaw.exe java"), ("All files", "*.*")],
        )
        if path:
            self.java_entry.delete(0, "end")
            self.java_entry.insert(0, path)

    def merge_local_versions(self):
        """Put anything sitting in versions/ at the top of the list, manifest or not."""
        try:
            installed = [v["id"] for v in minecraft_launcher_lib.utils.get_installed_versions(self.minecraft_directory)]
        except Exception:
            installed = []
        self.local_versions = set(installed)
        remote_only = [v for v in self.all_versions if v not in self.local_versions]
        self.all_versions = installed + remote_only
        self.apply_filter()

    # --- Version loading ---------------------------------------------------
    def load_all_versions(self):
        try:
            version_list = minecraft_launcher_lib.utils.get_version_list()
            versions = [v["id"] for v in version_list]
            default = next((v["id"] for v in version_list if v["type"] == "release"), None)
            status = "System cleared for operational departure."
        except Exception:
            versions = []
            default = None
            status = "Offline Mode Active. Local profiles only."

        def publish():
            self.all_versions = versions
            self.merge_local_versions()
            if default:
                self.version_combo.set(default)

        self.root.after(0, publish)
        self.set_status(status)

    # --- Local import ------------------------------------------------------
    def start_import(self):
        source = filedialog.askdirectory(
            title="Select a .minecraft folder, or a single folder from versions/"
        )
        if not source:
            return
        self.set_busy(True)
        self.progress.set(0.0)
        threading.Thread(target=self.import_sequence, args=(source,), daemon=True).start()

    @staticmethod
    def looks_like_version_folder(path):
        name = os.path.basename(os.path.normpath(path))
        return os.path.isfile(os.path.join(path, name + ".json"))

    @staticmethod
    def count_files(path):
        return sum(len(files) for _, _, files in os.walk(path))

    def count_top_level(self, path):
        """Files-to-copy total for a whole-folder import: recurse into subfolders,
        count top-level loose files as 1 each."""
        total = 0
        for entry in os.listdir(path):
            full = os.path.join(path, entry)
            total += self.count_files(full) if os.path.isdir(full) else 1
        return total

    def copy_top_level(self, source, counter):
        """Merge every entry in `source` (dirs and loose files alike) into
        self.minecraft_directory. This is the 'whole .minecraft folder' path,
        so nothing is whitelisted - mods, config, saves, options.txt, all of it."""
        os.makedirs(self.minecraft_directory, exist_ok=True)
        for entry in sorted(os.listdir(source)):
            s = os.path.join(source, entry)
            d = os.path.join(self.minecraft_directory, entry)
            if os.path.isdir(s):
                self.copy_tree(s, d, counter)
            else:
                try:
                    if not (os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s)):
                        shutil.copy2(s, d)
                except OSError as exc:
                    raise RuntimeError(f"Could not copy {entry}: {exc}") from exc
                counter[0] += 1
                if counter[0] % 25 == 0:
                    self.set_progress(counter[0])

    def copy_tree(self, src, dst, counter):
        """Merge src into dst, skipping files that already match in size."""
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            target = dst if rel == "." else os.path.join(dst, rel)
            os.makedirs(target, exist_ok=True)
            for name in files:
                s = os.path.join(root, name)
                d = os.path.join(target, name)
                try:
                    if not (os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s)):
                        shutil.copy2(s, d)
                except OSError as exc:
                    raise RuntimeError(f"Could not copy {name}: {exc}") from exc
                counter[0] += 1
                if counter[0] % 25 == 0:
                    self.set_progress(counter[0])

    def import_sequence(self, source):
        try:
            imported_ids = []

            if os.path.isdir(os.path.join(source, "versions")) or os.path.isdir(os.path.join(source, "libraries")):
                # A whole .minecraft folder (or a partial copy of one). Copy every
                # top-level entry as-is - versions, libraries, assets, mods,
                # config, resourcepacks, saves, options.txt, all of it - so a
                # straight copy of the source machine's folder just works here.
                self.set_status("Counting files to import...")
                total = self.count_top_level(source)
                self.set_max(total)
                counter = [0]
                self.set_status("Importing .minecraft folder...")
                self.copy_top_level(source, counter)
                versions_dir = os.path.join(source, "versions")
                if os.path.isdir(versions_dir):
                    imported_ids = sorted(
                        d for d in os.listdir(versions_dir)
                        if os.path.isdir(os.path.join(versions_dir, d))
                    )

            elif self.looks_like_version_folder(source):
                # A single folder out of versions/, e.g. 1.20.1-forge-47.3.0
                name = os.path.basename(os.path.normpath(source))
                self.set_max(self.count_files(source))
                self.set_status(f"Importing version {name} ...")
                self.copy_tree(source, os.path.join(self.minecraft_directory, "versions", name), [0])
                imported_ids = [name]

            else:
                raise RuntimeError(
                    "That folder doesn't look right. Pick either a .minecraft folder "
                    "(the one holding versions/ and libraries/), or one folder from "
                    "inside versions/ that contains a matching .json file."
                )

            self.set_progress(self._progress_max)
            warnings = self.check_imported(imported_ids)
            self.root.after(0, self.merge_local_versions)
            if imported_ids:
                self.root.after(0, lambda: self.version_combo.set(imported_ids[-1]))

            self.set_status(f"Imported {len(imported_ids)} version(s).")
            if warnings:
                self.root.after(0, lambda: messagebox.showwarning("Import Incomplete", "\n\n".join(warnings)))
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.set_status("Import aborted.")
            self.root.after(0, lambda: messagebox.showerror("Import Failure", message))
        finally:
            self.set_busy(False)

    def check_imported(self, version_ids):
        """Flag the two things people usually forget: the parent version and the libraries."""
        warnings = []
        for vid in version_ids:
            json_path = os.path.join(self.minecraft_directory, "versions", vid, vid + ".json")
            if not os.path.isfile(json_path):
                continue
            try:
                with open(json_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                warnings.append(f"{vid}: its .json file could not be read.")
                continue

            parent = data.get("inheritsFrom")
            if parent and not os.path.isfile(os.path.join(self.minecraft_directory, "versions", parent, parent + ".json")):
                warnings.append(
                    f"{vid} inherits from {parent}, which isn't installed here. "
                    f"Select {parent} once and launch it first, or copy that folder over too."
                )

            missing = 0
            for lib in data.get("libraries", []):
                artifact = lib.get("downloads", {}).get("artifact", {})
                rel = artifact.get("path")
                if rel and not os.path.isfile(os.path.join(self.minecraft_directory, "libraries", *rel.split("/"))):
                    missing += 1
            if missing:
                warnings.append(
                    f"{vid} is missing {missing} library file(s). Copy the whole libraries/ "
                    f"folder from the machine where it was installed."
                )
        return warnings

    # --- Launch ------------------------------------------------------------
    def start_launch_thread(self):
        username = self.username_entry.get().strip()
        version = self.version_combo.get().strip()
        use_forge = self.forge_var.get()
        if not username:
            messagebox.showwarning("Credential Alert", "An authentic runtime identity handle is required.")
            return
        if not version or version in (PLACEHOLDER_LOADING, PLACEHOLDER_EMPTY):
            messagebox.showwarning("Credential Alert", "Select a runtime version before departure.")
            return
        self.set_busy(True)
        self.progress.set(0.0)
        threading.Thread(target=self.launch_sequence, args=(version, username, use_forge), daemon=True).start()

    def launch_sequence(self, version, username, use_forge):
        try:
            callback = self.build_callback()
            launch_version = version
            already_local = version in self.local_versions

            if already_local:
                # Imported/offline version: don't try to reinstall it from the network.
                self.set_status(f"Using local profile {version}...")
            else:
                self.set_status("Assembling asset records...")
                minecraft_launcher_lib.install.install_minecraft_version(version, self.minecraft_directory, callback=callback)

            if use_forge:
                self.set_status("Resolving Matrix Framework build...")
                forge_version = minecraft_launcher_lib.forge.find_forge_version(version)
                if forge_version is None:
                    raise RuntimeError(f"No Forge build exists for Minecraft {version}.")
                minecraft_launcher_lib.forge.install_forge_version(forge_version, self.minecraft_directory, callback=callback)
                launch_version = minecraft_launcher_lib.forge.forge_to_installed_version(forge_version)

            options = {
                "username": username,
                "uuid": str(uuid.uuid4()),
                "token": "",
            }

            # If the user pointed us at a specific java(w).exe, force it -
            # this is what skips the broken PATH/auto-runtime lookup that
            # was causing the jli.dll crash.
            java_override = self.java_entry.get().strip()
            if java_override:
                if not os.path.isfile(java_override):
                    raise RuntimeError(f"Java executable not found at: {java_override}")
                options["executablePath"] = java_override
                options["defaultExecutablePath"] = java_override

            self.set_status("Igniting runtime...")
            launch_command = minecraft_launcher_lib.command.get_minecraft_command(launch_version, self.minecraft_directory, options)
            subprocess.Popen(launch_command)
            self.set_status("Launched. Enjoy your session.")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.set_status("Launch aborted.")
            self.root.after(0, lambda: messagebox.showerror("Launch Failure", message))
        finally:
            self.set_busy(False)


if __name__ == "__main__":
    app_root = ctk.CTk()
    launcher = ModernLauncher(app_root)
    app_root.mainloop()