"""The GUI must connect to the best location when nothing is selected.

Both combos start with the auto entry; Start without a choice passes no
--country/--city at all, and the engine resolves the location with the most
providers (getProviderSpec in cmd/urnetwork-vpn/main.go). Choosing a location
still has to reach the engine as flags.
"""
import importlib.util
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
    def __init__(self, text=""):
        self._text = text

    def get_text(self):
        return self._text

    def set_text(self, text):
        self._text = text


class _Combo:
    """Records append()/set_active() so combo filling can be checked."""

    def __init__(self, active_id=""):
        # A real combo starts with the auto entry; a non-empty active_id means
        # the user picked that location.
        self.rows = [("", "auto")]
        self.active = 1 if active_id else 0
        if active_id:
            self.rows.append((active_id, active_id))

    def remove_all(self):
        self.rows = []
        self.active = 0

    def append(self, row_id, text):
        self.rows.append((row_id, text))

    def set_active(self, index):
        self.active = index

    def get_active_id(self):
        if self.active is None or self.active >= len(self.rows):
            return None
        return self.rows[self.active][0]

    def set_tooltip_text(self, text):
        pass


class _Label:
    def __init__(self):
        self.text = ""

    def set_text(self, text):
        self.text = text


class GuiLocationDefaultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = load_gui_module()

    def app(self, country="", city="", mode="socks"):
        app = self.gui.URnetworkGUI.__new__(self.gui.URnetworkGUI)
        app.running = False
        app.process = None
        app.mode = mode
        app.verbose_log = False
        app.connected = False
        app.email_entry = _Entry("user@example.com")
        app.pass_entry = _Entry("secret")
        app.country_combo = _Combo(country)
        app.city_combo = _Combo(city)
        app.loc_label = _Label()
        app.messages = []
        app.location_hints = []
        app.started = []
        app.log = lambda msg: app.messages.append(msg)
        app.save_credentials = lambda: None
        app._start_engine = lambda: app.started.append(app._start_args)
        app._write_log_line = lambda text: None
        app.set_connected = lambda connected: setattr(app, "connected", connected)
        app._log_stream_line = lambda prefix, text: app.messages.append(prefix + text)
        app.set_location_hint = (
            lambda name, kind, auto=False: app.location_hints.append((name, kind, auto)))
        return app

    def test_start_without_location_lets_the_engine_pick(self):
        app = self.app()

        app.start()

        self.assertEqual(len(app.started), 1, "engine must start without a location")
        args, _env, country, city = app.started[0]
        self.assertEqual(args, [self.gui.SCRIPT, "socks"])
        self.assertEqual((country, city), ("", ""))
        self.assertFalse(any("No exit location" in m for m in app.messages))
        self.assertTrue(any("best location" in m for m in app.messages))

    def test_country_choice_is_forwarded(self):
        app = self.app(country="Netherlands")

        app.start()

        args = app.started[0][0]
        self.assertEqual(args, [self.gui.SCRIPT, "socks", "--country", "Netherlands"])

    def test_city_choice_is_forwarded(self):
        app = self.app(city="Amsterdam")

        app.start()

        args = app.started[0][0]
        self.assertEqual(args, [self.gui.SCRIPT, "socks", "--city", "Amsterdam"])

    def test_auto_entry_is_first_and_has_an_empty_id(self):
        app = self.app()
        combo = _Combo()

        app._fill_location_combo(combo, ["Netherlands", "Germany"], "")

        self.assertEqual(combo.rows[0], ("", self.gui.AUTO_LOCATION_LABEL))
        self.assertEqual(combo.rows[1], ("Netherlands", "Netherlands"))
        self.assertEqual(combo.active, 0)

    def test_reload_keeps_previous_choice(self):
        app = self.app(country="Germany")
        combo = _Combo()

        app._fill_location_combo(combo, ["Netherlands", "Germany"], "Germany")

        self.assertEqual(combo.get_active_id(), "Germany")

    def test_reload_falls_back_to_auto_when_choice_is_gone(self):
        app = self.app()
        combo = _Combo()

        app._fill_location_combo(combo, ["Netherlands"], "Atlantis")

        self.assertEqual(combo.active, 0)
        self.assertEqual(combo.get_active_id(), "")

    def test_auto_selected_line_updates_the_location_hint(self):
        app = self.app()

        app.handle_line('auto-selected "Netherlands" (country), provider count 400')

        self.assertEqual(app.location_hints, [("Netherlands", "country", True)])

    def test_matched_city_line_updates_the_location_hint(self):
        app = self.app()

        app.handle_line('city matched "Amsterdam", provider count 123')

        self.assertEqual(app.location_hints, [("Amsterdam", "city", False)])

    def test_set_location_hint_texts_the_label(self):
        app = self.app()

        self.gui.URnetworkGUI.set_location_hint(app, "Netherlands", "country", True)

        self.assertIn("Netherlands", app.loc_label.text)
        self.assertIn("auto", app.loc_label.text)


if __name__ == "__main__":
    unittest.main()
