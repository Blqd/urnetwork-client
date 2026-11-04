import importlib.util
import io
import os
import sys
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
    )
    repository.Gdk = types.SimpleNamespace()

    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository

    spec = importlib.util.spec_from_file_location("urnetwork_gui", GUI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GuiConnectionStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = load_gui_module()

    def app_for_mode(self, mode):
        app = self.gui.URnetworkGUI.__new__(self.gui.URnetworkGUI)
        app.mode = mode
        app.connected = False
        app.set_connected = lambda connected: setattr(app, "connected", connected)
        app._log_stream_line = lambda prefix, text: None
        return app

    def test_socks_listener_output_marks_gui_connected(self):
        app = self.app_for_mode("socks")

        app.on_output(io.StringIO("socks5 server is listening on :9999\n"), self.gui.GLib.IO_IN)

        self.assertTrue(app.connected)

    def test_vpn_connected_output_still_marks_gui_connected(self):
        app = self.app_for_mode("vpn")

        app.on_output(io.StringIO("VPN connected. TUN: urnetwork, IP: 10.0.0.1/24\n"), self.gui.GLib.IO_IN)

        self.assertTrue(app.connected)


if __name__ == "__main__":
    unittest.main()
