import importlib.util
import os
import sys
import tempfile
import types
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GUI_PATH = os.path.join(ROOT, "gui", "urnetwork-gui.py")


def load_gui_module():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args, **kwargs: None

    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace()
    repository.GLib = types.SimpleNamespace(
        IO_HUP=16,
        IO_IN=1,
        idle_add=lambda fn, *args: fn(*args),
        timeout_add=lambda ms, fn: 1,
    )
    repository.Gdk = types.SimpleNamespace()

    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository

    spec = importlib.util.spec_from_file_location("urnetwork_gui", GUI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Entry:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text

    def set_text(self, text):
        self._text = text


class GuiHardeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = load_gui_module()

    def app(self):
        app = self.gui.URnetworkGUI.__new__(self.gui.URnetworkGUI)
        app.verbose_log = False
        app._pending_log = []
        app._flush_id = 0
        app.log = lambda msg: None
        return app

    def test_sudo_env_points_at_askpass_helper(self):
        """VPN children must get SUDO_ASKPASS so sudo can open a terminal."""
        base = {"BASE": "1"}
        env = self.gui.sudo_env(base)
        self.assertTrue(self.gui.ASKPASS.endswith("urnetwork-askpass.sh"))
        self.assertEqual(env["SUDO_ASKPASS"], self.gui.ASKPASS)
        self.assertEqual(env["SUDO_ASKPASS_REQUIRE"], "prefer")
        self.assertEqual(env["URNETWORK_FORCE_ASKPASS"], "1")
        self.assertEqual(env["BASE"], "1")
        # The caller's dict must not be mutated.
        self.assertNotIn("SUDO_ASKPASS", base)

    def test_askpass_helper_is_executable(self):
        """The GUI relies on sudo being able to exec the helper."""
        self.assertTrue(os.access(self.gui.ASKPASS, os.X_OK), self.gui.ASKPASS)

    def test_save_credentials_is_atomic_0600_without_quotes(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as tmp:
            self.gui.CONFIG_DIR = tmp
            self.gui.AUTH_FILE = os.path.join(tmp, "auth")
            app.email_entry = _Entry("user@example.com")
            app.pass_entry = _Entry("p@ss'word")
            app.net_entry = _Entry("")
            original_set, original_get = self._patch_keyring(self.gui, set_result=False)
            self.addCleanup(lambda: (setattr(self.gui, "keyring_set", original_set),
                                     setattr(self.gui, "keyring_get", original_get)))

            app.save_credentials()

            with open(self.gui.AUTH_FILE) as f:
                content = f.read()
            self.assertEqual(
                content,
                "USER_AUTH=user@example.com\nPASSWORD=p@ss'word\n",
            )
            mode = os.stat(self.gui.AUTH_FILE).st_mode & 0o777
            self.assertEqual(mode, 0o600)
            self.assertFalse(os.path.exists(self.gui.AUTH_FILE + ".tmp"))

    def test_save_credentials_rejects_line_breaks(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as tmp:
            self.gui.CONFIG_DIR = tmp
            self.gui.AUTH_FILE = os.path.join(tmp, "auth")
            app.email_entry = _Entry("user@example.com\nPASSWORD=evil")
            app.pass_entry = _Entry("secret")
            app.net_entry = _Entry("")
            self._patch_keyring(self.gui, set_result=False)

            app.save_credentials()

            self.assertFalse(os.path.exists(self.gui.AUTH_FILE))

    @staticmethod
    def _patch_keyring(module, set_result=False, get_result=""):
        """Replace the keyring calls so tests never touch the real keyring."""
        original_set, original_get = module.keyring_set, module.keyring_get
        module.keyring_set = lambda email, password: set_result
        module.keyring_get = lambda email: get_result
        return original_set, original_get

    def test_save_credentials_uses_secret_store_when_available(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as tmp:
            self.gui.CONFIG_DIR = tmp
            self.gui.AUTH_FILE = os.path.join(tmp, "auth")
            app.email_entry = _Entry("user@example.com")
            app.pass_entry = _Entry("p@ss'word")
            app.net_entry = _Entry("")
            original_set, original_get = self._patch_keyring(self.gui, set_result=True)
            self.addCleanup(lambda: (setattr(self.gui, "keyring_set", original_set),
                                     setattr(self.gui, "keyring_get", original_get)))

            app.save_credentials()

            with open(self.gui.AUTH_FILE) as f:
                content = f.read()
            self.assertNotIn("PASSWORD=", content)
            self.assertIn("USER_AUTH=user@example.com", content)
            self.assertIn("system secret store", content)
            self.assertEqual(os.stat(self.gui.AUTH_FILE).st_mode & 0o777, 0o600)

    def test_load_credentials_reads_password_from_secret_store(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as tmp:
            self.gui.CONFIG_DIR = tmp
            self.gui.AUTH_FILE = os.path.join(tmp, "auth")
            with open(self.gui.AUTH_FILE, "w") as f:
                f.write("USER_AUTH=user@example.com\n# password is stored in the system secret store\n")
            app.email_entry = _Entry("")
            app.pass_entry = _Entry("")
            app.net_entry = _Entry("")
            original_set, original_get = self._patch_keyring(self.gui, get_result="from-keyring")
            self.addCleanup(lambda: (setattr(self.gui, "keyring_set", original_set),
                                     setattr(self.gui, "keyring_get", original_get)))

            app.load_credentials()

            self.assertEqual(app.email_entry.get_text(), "user@example.com")
            self.assertEqual(app.pass_entry.get_text(), "from-keyring")

    def test_load_credentials_migrates_plaintext_password(self):
        app = self.app()
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            self.gui.CONFIG_DIR = tmp
            self.gui.AUTH_FILE = os.path.join(tmp, "auth")
            with open(self.gui.AUTH_FILE, "w") as f:
                f.write("USER_AUTH=user@example.com\nPASSWORD=plain-pw\n")
            app.email_entry = _Entry("")
            app.pass_entry = _Entry("")
            app.net_entry = _Entry("")
            original_set, original_get = self._patch_keyring(self.gui)
            self.gui.keyring_set = lambda email, password: calls.append((email, password)) or True
            self.addCleanup(lambda: (setattr(self.gui, "keyring_set", original_set),
                                     setattr(self.gui, "keyring_get", original_get)))

            app.load_credentials()

            self.assertEqual(calls, [("user@example.com", "plain-pw")])
            with open(self.gui.AUTH_FILE) as f:
                content = f.read()
            self.assertNotIn("PASSWORD=plain-pw", content)

    def test_glog_lines_are_not_persisted_unless_verbose(self):
        app = self.app()
        written, queued = [], []
        app._write_log_line = lambda msg: written.append(msg)
        app._queue_log = lambda msg: queued.append(msg)

        app._log_stream_line("[proc] ", "I0605 21:24:57 transfer.go:4416] noisy")
        self.assertEqual(written, [])
        self.assertEqual(queued, [])

        app._log_stream_line("[proc] ", "VPN connected. TUN: urnetwork, IP: 10.0.0.1/24")
        self.assertEqual(len(written), 1)
        self.assertEqual(len(queued), 1)

    def test_verbose_keeps_glog_lines(self):
        app = self.app()
        app.verbose_log = True
        written = []
        app._write_log_line = lambda msg: written.append(msg)
        app._queue_log = lambda msg: None

        app._log_stream_line("[proc] ", "I0605 21:24:57 transfer.go:4416] noisy")

        self.assertEqual(len(written), 1)

    def test_handle_line_marks_connected_once(self):
        app = self.app()
        app.mode = "socks"
        app.connected = False
        calls = []
        app.set_connected = lambda value: calls.append(value)
        app._log_stream_line = lambda prefix, text: None

        app.handle_line("socks5 server is listening on 127.0.0.1:9999")
        app.handle_line("socks5 server is listening on 127.0.0.1:9999")

        self.assertEqual(calls, [True])


    def test_stdout_hup_flushes_partial_line(self):
        """A crash ends the stream without a newline; that tail must be kept."""
        app = self.app()
        lines = []
        app.handle_line = lines.append
        app._stdout_buf = b"last words without newline"

        self.assertFalse(app._on_stdout_readable(0, self.gui.GLib.IO_HUP))
        self.assertEqual(lines, ["last words without newline"])
        self.assertEqual(app._stdout_buf, b"")

    def test_stdout_eof_flushes_partial_line(self):
        app = self.app()
        lines = []
        app.handle_line = lines.append
        r, w = os.pipe()
        os.write(w, b"partial tail")
        os.close(w)
        try:
            app._stdout_buf = b""
            self.assertTrue(app._on_stdout_readable(r, self.gui.GLib.IO_IN))
            self.assertFalse(app._on_stdout_readable(r, self.gui.GLib.IO_IN))
        finally:
            os.close(r)
        self.assertEqual(lines, ["partial tail"])

    def test_log_buffer_is_capped(self):
        app = self.app()
        written = []
        app._write_log_line = lambda msg: written.append(msg)

        class _Buffer:
            def __init__(self):
                self.text = ""

            def get_end_iter(self):
                return len(self.text)

            def get_start_iter(self):
                return 0

            def get_char_count(self):
                return len(self.text)

            def get_iter_at_offset(self, offset):
                return offset

            def insert(self, at, data):
                self.text = self.text[:at] + data

            def delete(self, start, end):
                self.text = self.text[:start] + self.text[end:]

            def create_mark(self, *args, **kwargs):
                return None

        app.log_buffer = _Buffer()
        app.log_view = types.SimpleNamespace(scroll_to_mark=lambda *a: None)
        app._pending_log = ["x" * 1000] * 1000

        app._flush_log()

        self.assertLessEqual(len(app.log_buffer.text), self.gui.LOG_BUFFER_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
