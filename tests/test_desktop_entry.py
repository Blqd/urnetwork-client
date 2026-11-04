import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENTRY = os.path.join(ROOT, "gui", "urnetwork-vpn.desktop")
LAUNCHER = os.path.join(ROOT, "scripts", "urnetwork-gui-launch.sh")
INSTALLER = os.path.join(ROOT, "scripts", "install-desktop-entry.sh")


def read_entry():
    with open(ENTRY, encoding="utf-8") as fh:
        return fh.read()


def entry_keys():
    keys = {}
    for line in read_entry().splitlines():
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            keys[key] = value
    return keys


class DesktopEntryTest(unittest.TestCase):
    def test_desktop_file_validates(self):
        """desktop-file-validate must pass; unregistered Categories break desktops."""
        if not shutil.which("desktop-file-validate"):
            self.skipTest("desktop-file-validate not installed")
        proc = subprocess.run(
            ["desktop-file-validate", ENTRY], text=True, capture_output=True
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_required_keys(self):
        keys = entry_keys()
        self.assertEqual(keys.get("Type"), "Application")
        self.assertEqual(keys.get("Terminal"), "false")
        self.assertEqual(keys.get("Categories"), "Network;")
        self.assertTrue(keys.get("Icon"))
        self.assertTrue(keys.get("Name"))
        # The entry must launch the wrapper, never a bare interpreter: "python3"
        # resolves to a Homebrew build without PyGObject on this machine.
        self.assertIn("urnetwork-gui-launch.sh", keys.get("Exec", ""))
        self.assertNotIn("python3", keys.get("Exec", ""))

    def test_executables_exist_and_are_executable(self):
        keys = entry_keys()
        exec_path = keys["Exec"].split()[0]
        self.assertTrue(os.path.isfile(exec_path), exec_path)
        self.assertTrue(os.access(exec_path, os.X_OK), exec_path)
        try_exec = keys.get("TryExec")
        if try_exec:
            self.assertTrue(os.path.isfile(try_exec), try_exec)
            self.assertTrue(os.access(try_exec, os.X_OK), try_exec)

    def test_startup_wm_class_matches_gui(self):
        """Without a matching WMClass the dock shows a second, generic icon."""
        keys = entry_keys()
        self.assertEqual(keys.get("StartupWMClass"), "urnetwork-gui.py")
        gui = os.path.join(ROOT, "gui", "urnetwork-gui.py")
        self.assertEqual(os.path.basename(gui), keys["StartupWMClass"])

    def test_categories_are_registered(self):
        """Any non X- category must come from the freedesktop registered list."""
        allowed = {
            "AudioVideo", "Audio", "Video", "Development", "Education", "Game",
            "Graphics", "Network", "Office", "Science", "Settings", "System",
            "Utility",
        }
        categories = [c for c in entry_keys()["Categories"].split(";") if c]
        unknown = [c for c in categories if c not in allowed and not c.startswith("X-")]
        self.assertEqual(unknown, [])


class LauncherScriptTest(unittest.TestCase):
    def _launcher_env(self, workdir):
        stubs = os.path.join(workdir, "stubs")
        os.makedirs(stubs, exist_ok=True)
        for name in ("notify-send", "zenity"):
            path = os.path.join(stubs, name)
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        env = os.environ.copy()
        env["PATH"] = stubs + ":" + env.get("PATH", "/usr/bin:/bin")
        env["XDG_CONFIG_HOME"] = os.path.join(workdir, "config")
        return env

    def test_shell_syntax(self):
        proc = subprocess.run(["bash", "-n", LAUNCHER], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = subprocess.run(["bash", "-n", INSTALLER], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_missing_gui_fails_loudly(self):
        """A broken install must exit non-zero, not die silently under Terminal=false."""
        with tempfile.TemporaryDirectory() as tmp:
            scripts = os.path.join(tmp, "scripts")
            os.makedirs(scripts)
            fake = os.path.join(scripts, "urnetwork-gui-launch.sh")
            shutil.copy2(LAUNCHER, fake)
            proc = subprocess.run(
                [fake], env=self._launcher_env(tmp), capture_output=True, text=True
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            log = os.path.join(tmp, "config", "urnetwork", "launcher.log")
            self.assertTrue(os.path.isfile(log))
            with open(log, encoding="utf-8") as fh:
                self.assertIn("ERROR", fh.read())

    def test_no_display_fails_loudly(self):
        env = self._launcher_env(tempfile.mkdtemp())
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        proc = subprocess.run([LAUNCHER], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 3, proc.stderr)

    def test_picks_interpreter_with_gtk_bindings(self):
        """The wrapper must skip interpreters without `gi` (e.g. Homebrew python3)."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "badpython")
            with open(bad, "w") as fh:
                fh.write("#!/bin/sh\nexit 1\n")
            os.chmod(bad, 0o755)
            env = self._launcher_env(tmp)
            env["URNETWORK_PYTHON"] = bad
            env.pop("DISPLAY", None)
            env.pop("WAYLAND_DISPLAY", None)
            proc = subprocess.run([LAUNCHER], env=env, capture_output=True, text=True)
            # Reaching the display check (3) proves the bad interpreter was skipped
            # in favour of one that can import gi.
            self.assertEqual(proc.returncode, 3, proc.stderr)


class GuiMarkupTest(unittest.TestCase):
    def test_no_invalid_pango_font_size(self):
        """Pango spans need size="small"/numeric, not font-size="11px" (CSS syntax)."""
        gui = os.path.join(ROOT, "gui", "urnetwork-gui.py")
        with open(gui, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(re.findall(r'<span[^>]*font-size=', text), [])


if __name__ == "__main__":
    unittest.main()
