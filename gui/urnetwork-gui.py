#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, GLib, Gdk
import subprocess
import os
import signal
import threading
import re

CONFIG_DIR = os.path.expanduser("~/.config/urnetwork")
AUTH_FILE = os.path.join(CONFIG_DIR, "auth")
BUILD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "build"))
SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "urnetwork-connect.sh"))

GLOG_RE = re.compile(r'^[IWEF]\d{4}\s')


class URnetworkGUI:
    def __init__(self):
        self.process = None
        self.mode = "socks"
        self.running = False
        self.connected = False
        self.verbose_log = False

        css = b"""
        window { background: #1a1b2e; }
        .title { font-size: 20px; font-weight: bold; color: #e0e0ff; }
        .status-off { color: #666666; font-size: 13px; }
        .status-on { color: #00ff88; font-size: 13px; font-weight: bold; }
        .status-connecting { color: #ffab00; font-size: 13px; font-weight: bold; }
        .label { color: #8888aa; font-size: 13px; }
        .value { color: #e0e0ff; font-size: 13px; font-weight: bold; }
        .btn-toggle {
            font-size: 16px; font-weight: bold; min-height: 44px;
            border-radius: 22px;
        }
        .btn-start {
            background: #00c853; color: white;
        }
        .btn-stop {
            background: #ff1744; color: white;
        }
        .frame { border-radius: 8px; }
        textview text { background: #0d0e1a; color: #a0a0c0; }
        """

        style = Gtk.CssProvider()
        style.load_from_data(css)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.window = Gtk.Window(title="URnetwork VPN")
        self.window.set_default_size(380, 540)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", self.on_destroy)
        self.window.connect("delete-event", self.on_delete)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_top(20)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        self.window.add(vbox)

        title = Gtk.Label(label="URnetwork VPN")
        title.get_style_context().add_class("title")
        vbox.pack_start(title, False, False, 0)

        status_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_hbox.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(status_hbox, False, False, 0)

        self.status_icon = Gtk.Label()
        self.status_icon.set_markup('<span foreground="#666666">●</span>')
        status_hbox.pack_start(self.status_icon, False, False, 0)

        self.status_text = Gtk.Label()
        self.status_text.set_markup('<span foreground="#666666">Disconnected</span>')
        status_hbox.pack_start(self.status_text, False, False, 0)

        vbox.pack_start(Gtk.Label(), False, False, 4)

        auth_frame = Gtk.Frame()
        auth_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        auth_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        auth_vbox.set_margin_top(8)
        auth_vbox.set_margin_bottom(8)
        auth_vbox.set_margin_start(8)
        auth_vbox.set_margin_end(8)
        auth_frame.add(auth_vbox)

        auth_label = Gtk.Label(label="Account")
        auth_label.get_style_context().add_class("label")
        auth_vbox.pack_start(auth_label, False, False, 0)

        email_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        email_row.pack_start(Gtk.Label(label="Email:", width_chars=8), False, False, 0)
        self.email_entry = Gtk.Entry()
        self.email_entry.set_placeholder_text("email@example.com")
        email_row.pack_start(self.email_entry, True, True, 0)
        auth_vbox.pack_start(email_row, False, False, 0)

        pass_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pass_row.pack_start(Gtk.Label(label="Password:", width_chars=8), False, False, 0)
        self.pass_entry = Gtk.Entry()
        self.pass_entry.set_placeholder_text("password")
        self.pass_entry.set_visibility(False)
        pass_row.pack_start(self.pass_entry, True, True, 0)
        auth_vbox.pack_start(pass_row, False, False, 0)

        net_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        net_row.pack_start(Gtk.Label(label="Network:", width_chars=8), False, False, 0)
        self.net_entry = Gtk.Entry()
        self.net_entry.set_placeholder_text("yourname.ur.network")
        net_row.pack_start(self.net_entry, True, True, 0)
        auth_vbox.pack_start(net_row, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.set_size_request(-1, 28)
        self.save_btn.connect("clicked", lambda b: self.save_credentials())
        btn_row.pack_end(self.save_btn, False, False, 0)
        auth_vbox.pack_start(btn_row, False, False, 0)

        vbox.pack_start(auth_frame, False, False, 0)

        mode_frame = Gtk.Frame()
        mode_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        mode_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mode_vbox.set_margin_top(8)
        mode_vbox.set_margin_bottom(8)
        mode_vbox.set_margin_start(8)
        mode_vbox.set_margin_end(8)
        mode_frame.add(mode_vbox)

        mode_label = Gtk.Label(label="Mode")
        mode_label.get_style_context().add_class("label")
        mode_vbox.pack_start(mode_label, False, False, 0)

        mode_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_vbox.pack_start(mode_buttons, False, False, 0)

        self.btn_socks = Gtk.RadioButton.new_with_label(None, "SOCKS5")
        self.btn_socks.connect("toggled", self.on_mode, "socks")
        mode_buttons.pack_start(self.btn_socks, False, False, 0)

        self.btn_vpn = Gtk.RadioButton.new_with_label_from_widget(self.btn_socks, "VPN (TUN)")
        self.btn_vpn.connect("toggled", self.on_mode, "vpn")
        mode_buttons.pack_start(self.btn_vpn, False, False, 0)

        loc_frame = Gtk.Frame()
        loc_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        loc_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        loc_vbox.set_margin_top(8)
        loc_vbox.set_margin_bottom(8)
        loc_vbox.set_margin_start(8)
        loc_vbox.set_margin_end(8)
        loc_frame.add(loc_vbox)

        loc_label = Gtk.Label(label="Exit location")
        loc_label.get_style_context().add_class("label")
        loc_vbox.pack_start(loc_label, False, False, 0)

        country_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        country_row.pack_start(Gtk.Label(label="Country:", width_chars=8), False, False, 0)
        self.country_combo = Gtk.ComboBoxText()
        self.country_combo.set_entry_text_column(0)
        self.country_combo.append("", "")
        self.country_combo.set_active(0)
        country_row.pack_start(self.country_combo, True, True, 0)
        loc_vbox.pack_start(country_row, False, False, 0)

        city_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        city_row.pack_start(Gtk.Label(label="City:", width_chars=8), False, False, 0)
        self.city_combo = Gtk.ComboBoxText()
        self.city_combo.set_entry_text_column(0)
        self.city_combo.append("", "")
        self.city_combo.set_active(0)
        city_row.pack_start(self.city_combo, True, True, 0)
        loc_vbox.pack_start(city_row, False, False, 0)

        loc_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.loc_btn = Gtk.Button(label="⟳  Load locations")
        self.loc_btn.set_size_request(-1, 24)
        self.loc_btn.connect("clicked", self.on_list_locations)
        loc_btn_row.pack_end(self.loc_btn, False, False, 0)
        loc_vbox.pack_start(loc_btn_row, False, False, 0)

        vbox.pack_start(mode_frame, False, False, 0)
        vbox.pack_start(loc_frame, False, False, 0)

        self.toggle_btn = Gtk.Button(label="▶  Start")
        self.toggle_btn.get_style_context().add_class("btn-toggle")
        self.toggle_btn.get_style_context().add_class("btn-start")
        self.toggle_btn.set_size_request(-1, 48)
        self.toggle_btn.connect("clicked", self.on_toggle)
        vbox.pack_start(self.toggle_btn, False, False, 6)

        proxy_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        proxy_hbox.set_halign(Gtk.Align.CENTER)
        self.proxy_btn = Gtk.Button(label="Set SOCKS5 Proxy")
        self.proxy_btn.set_size_request(-1, 26)
        self.proxy_btn.connect("clicked", self.on_proxy_set)
        proxy_hbox.pack_start(self.proxy_btn, False, False, 0)
        self.proxy_clear_btn = Gtk.Button(label="Clear Proxy")
        self.proxy_clear_btn.set_size_request(-1, 26)
        self.proxy_clear_btn.connect("clicked", self.on_proxy_clear)
        proxy_hbox.pack_start(self.proxy_clear_btn, False, False, 0)
        vbox.pack_start(proxy_hbox, False, False, 2)

        self.proxy_active = Gtk.Label()
        self.proxy_active.set_markup('<span foreground="#666666" font-size="11px"></span>')
        vbox.pack_start(self.proxy_active, False, False, 0)

        log_frame = Gtk.Frame()
        log_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(120)
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_buffer = self.log_view.get_buffer()
        log_scroll.add(self.log_view)
        log_frame.add(log_scroll)

        log_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.verbose_check = Gtk.CheckButton(label="Verbose logs")
        self.verbose_check.connect("toggled", self.on_verbose_toggle)
        log_toolbar.pack_start(self.verbose_check, False, False, 0)
        self.clear_log_btn = Gtk.Button(label="Clear")
        self.clear_log_btn.set_size_request(-1, 20)
        self.clear_log_btn.connect("clicked", self.on_clear_log)
        log_toolbar.pack_end(self.clear_log_btn, False, False, 0)
        log_toolbar.set_margin_bottom(2)
        vbox.pack_start(log_toolbar, False, False, 0)
        vbox.pack_start(log_frame, True, True, 0)

        self.load_credentials()
        self.window.show_all()
        GLib.idle_add(self.fetch_locations)

    def load_credentials(self):
        if os.path.exists(AUTH_FILE):
            with open(AUTH_FILE) as f:
                for line in f:
                    line = line.strip()
                    if '=' in line:
                        k, v = line.split('=', 1)
                        v = v.strip("'\"")
                        if k == "USER_AUTH":
                            self.email_entry.set_text(v)
                        elif k == "PASSWORD":
                            self.pass_entry.set_text(v)
                        elif k == "NETWORK_NAME":
                            self.net_entry.set_text(v)

    def save_credentials(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(AUTH_FILE, "w") as f:
            f.write("USER_AUTH={}\n".format(self.email_entry.get_text().strip()))
            f.write("PASSWORD={}\n".format(self.pass_entry.get_text()))
            net = self.net_entry.get_text().strip()
            if net:
                f.write("NETWORK_NAME={}\n".format(net))
        self.log("✓ Credentials saved")

    def log(self, msg):
        GLib.idle_add(self._log, msg)

    def _log(self, msg):
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, msg + "\n")
        mark = self.log_buffer.create_mark(None, end, False)
        self.log_view.scroll_to_mark(mark, 0, True, 0, 1)
        return False

    def parse_locations(self, text):
        countries, regions, cities = [], [], []
        current = None
        for line in text.splitlines():
            s = line.strip()
            if s == "countries:":
                current = "countries"
            elif s == "regions:":
                current = "regions"
            elif s == "cities:":
                current = "cities"
            elif s and line.startswith("  ") and current:
                if current == "countries":
                    countries.append(s)
                elif current == "regions":
                    regions.append(s)
                elif current == "cities":
                    cities.append(s)
        return countries, regions, cities

    def populate_locations(self, countries, regions, cities):
        self.country_combo.remove_all()
        self.country_combo.append("", "")
        for c in countries:
            self.country_combo.append(c, c)
        self.country_combo.set_active(0)

        self.city_combo.remove_all()
        self.city_combo.append("", "")
        for c in cities:
            self.city_combo.append(c, c)
        self.city_combo.set_active(0)

        self.log("✓ Locations loaded: {} countries, {} cities".format(len(countries), len(cities)))

    def fetch_locations(self):
        def _run():
            try:
                env = os.environ.copy()
                env["USER_AUTH"] = self.email_entry.get_text().strip()
                env["PASSWORD"] = self.pass_entry.get_text()
                if not env["USER_AUTH"] or not env["PASSWORD"]:
                    return
                proc = subprocess.Popen(
                    [SCRIPT, "list-locations"], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True
                )
                out, _ = proc.communicate(timeout=30)
                countries, regions, cities = self.parse_locations(out)
                if countries or cities:
                    GLib.idle_add(self.populate_locations, countries, regions, cities)
            except Exception as e:
                GLib.idle_add(self.log, "⚠ Locations: " + str(e))
        GLib.idle_add(self.log, "⟳ Loading locations...")
        threading.Thread(target=_run, daemon=True).start()

    def on_list_locations(self, btn):
        self.save_credentials()
        self.fetch_locations()

    def on_verbose_toggle(self, btn):
        self.verbose_log = btn.get_active()

    def on_clear_log(self, btn):
        self.log_buffer.set_text("")

    def on_mode(self, btn, mode):
        if btn.get_active():
            self.mode = mode

    def on_toggle(self, btn):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        self.save_credentials()
        env = os.environ.copy()
        env["USER_AUTH"] = self.email_entry.get_text().strip()
        env["PASSWORD"] = self.pass_entry.get_text()

        args = [SCRIPT, self.mode]
        country = self.country_combo.get_active_text() or ""
        if country:
            args.extend(["--country", country])
        city = self.city_combo.get_active_text() or ""
        if city:
            args.extend(["--city", city])

        if not country and not city:
            self.log("⚠  No location set — click 'List locations' to see options, then enter Country or City")
            self.log("▶  Starting anyway (will fail without location)...")
        self.log("▶  Starting " + self.mode.upper() + "...")

        self.connected = False
        self.set_connecting(True)
        self.process = subprocess.Popen(
            args, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, preexec_fn=os.setsid
        )
        self.running = True
        self.toggle_btn.set_label("⏹  Stop")
        self.toggle_btn.get_style_context().remove_class("btn-start")
        self.toggle_btn.get_style_context().add_class("btn-stop")

        GLib.io_add_watch(
            self.process.stdout, GLib.IO_IN,
            self.on_output
        )
        GLib.child_watch_add(
            self.process.pid, self.on_process_end
        )

    def stop(self):
        self.log("⏹  Stopping...")
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except:
                pass
            GLib.timeout_add(200, self._poll_stop)
        else:
            self._finish_stop()

    def _poll_stop(self):
        if not self.process:
            self._finish_stop()
            return False
        try:
            self.process.wait(timeout=0)
            self.process = None
            self._finish_stop()
            return False
        except:
            return True

    def _finish_stop(self):
        if not self.running:
            return
        subprocess.run([SCRIPT, "off"], capture_output=True)
        self.running = False
        self.connected = False
        self.toggle_btn.set_label("▶  Start")
        self.toggle_btn.get_style_context().remove_class("btn-stop")
        self.toggle_btn.get_style_context().add_class("btn-start")
        self.set_connected(False)
        self.log("✓ Stopped")

    def on_output(self, source, condition):
        if condition == GLib.IO_HUP:
            return False
        line = source.readline()
        if line:
            text = line.rstrip()
            if text == "VPN connected.":
                self.connected = True
                GLib.idle_add(self.set_connected, True)
            if self.verbose_log or not GLOG_RE.match(text):
                self.log(text)
        return True

    def on_process_end(self, pid, status):
        self.running = False
        self.process = None
        self.toggle_btn.set_label("▶  Start")
        self.toggle_btn.get_style_context().remove_class("btn-stop")
        self.toggle_btn.get_style_context().add_class("btn-start")
        if not self.connected:
            self.log("⚠ VPN failed to connect")
        self.set_connected(False)

    def set_connecting(self, state):
        if state:
            self.connected = False
            self.status_icon.set_markup('<span foreground="#ffab00">●</span>')
            self.status_text.set_markup('<span foreground="#ffab00" weight="bold">Connecting...</span>')
        else:
            self.set_connected(False)

    def set_connected(self, connected):
        self.connected = connected
        if connected:
            self.status_icon.set_markup('<span foreground="#00ff88">●</span>')
            self.status_text.set_markup('<span foreground="#00ff88" weight="bold">Connected</span>')
        else:
            self.status_icon.set_markup('<span foreground="#666666">●</span>')
            self.status_text.set_markup('<span foreground="#666666">Disconnected</span>')

    def set_system_proxy(self, state):
        try:
            if state:
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"],
                            capture_output=True)
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "host", "127.0.0.1"],
                            capture_output=True)
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.socks", "port", "9999"],
                            capture_output=True)
            else:
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "none"],
                            capture_output=True)
        except Exception as e:
            self.log("⚠ Proxy error: " + str(e))
            return False
        return True

    def on_proxy_set(self, btn):
        if self.set_system_proxy(True):
            self.proxy_active.set_markup(
                '<span foreground="#00ff88" font-size="11px">● SOCKS5 127.0.0.1:9999</span>'
            )
            self.log("✓ System proxy set to SOCKS5 127.0.0.1:9999")

    def on_proxy_clear(self, btn):
        if self.set_system_proxy(False):
            self.proxy_active.set_markup(
                '<span foreground="#666666" font-size="11px"></span>'
            )
            self.log("✓ System proxy cleared")

    def on_delete(self, window, event):
        self.save_credentials()
        return False

    def on_destroy(self, window):
        self.stop()
        Gtk.main_quit()


if __name__ == "__main__":
    app = URnetworkGUI()
    Gtk.main()
