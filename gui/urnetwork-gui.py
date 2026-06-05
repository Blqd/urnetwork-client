#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, GLib, Gdk
import subprocess
import os
import signal

CONFIG_DIR = os.path.expanduser("~/.config/urnetwork")
AUTH_FILE = os.path.join(CONFIG_DIR, "auth")
BUILD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "build"))
SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "urnetwork-connect.sh"))


class URnetworkGUI:
    def __init__(self):
        self.process = None
        self.mode = "socks"
        self.running = False

        css = b"""
        window { background: #1a1b2e; }
        .title { font-size: 20px; font-weight: bold; color: #e0e0ff; }
        .status-off { color: #666666; font-size: 13px; }
        .status-on { color: #00ff88; font-size: 13px; font-weight: bold; }
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
        self.window.set_default_size(380, 480)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", self.on_destroy)

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
        self.country_entry = Gtk.Entry()
        self.country_entry.set_placeholder_text("Auto")
        country_row.pack_start(self.country_entry, True, True, 0)
        loc_vbox.pack_start(country_row, False, False, 0)

        city_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        city_row.pack_start(Gtk.Label(label="City:", width_chars=8), False, False, 0)
        self.city_entry = Gtk.Entry()
        self.city_entry.set_placeholder_text("Auto")
        city_row.pack_start(self.city_entry, True, True, 0)
        loc_vbox.pack_start(city_row, False, False, 0)

        vbox.pack_start(mode_frame, False, False, 0)
        vbox.pack_start(loc_frame, False, False, 0)

        self.toggle_btn = Gtk.Button(label="▶  Start")
        self.toggle_btn.get_style_context().add_class("btn-toggle")
        self.toggle_btn.get_style_context().add_class("btn-start")
        self.toggle_btn.set_size_request(-1, 48)
        self.toggle_btn.connect("clicked", self.on_toggle)
        vbox.pack_start(self.toggle_btn, False, False, 6)

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
        vbox.pack_start(log_frame, True, True, 0)

        self.window.show_all()

    def log(self, msg):
        GLib.idle_add(self._log, msg)

    def _log(self, msg):
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, msg + "\n")
        mark = self.log_buffer.create_mark(None, end, False)
        self.log_view.scroll_to_mark(mark, 0, True, 0, 1)
        return False

    def on_mode(self, btn, mode):
        if btn.get_active():
            self.mode = mode

    def on_toggle(self, btn):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        env = os.environ.copy()
        if os.path.exists(AUTH_FILE):
            with open(AUTH_FILE) as f:
                for line in f:
                    line = line.strip()
                    if '=' in line:
                        k, v = line.split('=', 1)
                        v = v.strip("'\"")
                        env[k] = v

        args = [SCRIPT, self.mode]
        country = self.country_entry.get_text().strip()
        if country:
            args.extend(["--country", country])
        city = self.city_entry.get_text().strip()
        if city:
            args.extend(["--city", city])

        self.log("▶  Starting " + self.mode.upper() + "...")
        self.process = subprocess.Popen(
            args, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, preexec_fn=os.setsid
        )
        self.running = True
        self.toggle_btn.set_label("⏹  Stop")
        self.toggle_btn.get_style_context().remove_class("btn-start")
        self.toggle_btn.get_style_context().add_class("btn-stop")
        self.set_status(True)

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
                self.process.wait(timeout=5)
            except:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except:
                    pass
            self.process = None
        subprocess.run([SCRIPT, "off"], capture_output=True)
        self.running = False
        self.toggle_btn.set_label("▶  Start")
        self.toggle_btn.get_style_context().remove_class("btn-stop")
        self.toggle_btn.get_style_context().add_class("btn-start")
        self.set_status(False)
        self.log("✓ Stopped")

    def on_output(self, source, condition):
        if condition == GLib.IO_HUP:
            return False
        line = source.readline()
        if line:
            self.log(line.rstrip())
        return True

    def on_process_end(self, pid, status):
        self.running = False
        self.toggle_btn.set_label("▶  Start")
        self.toggle_btn.get_style_context().remove_class("btn-stop")
        self.toggle_btn.get_style_context().add_class("btn-start")
        self.set_status(False)
        self.log("■ Process ended")

    def set_status(self, connected):
        if connected:
            self.status_icon.set_markup('<span foreground="#00ff88">●</span>')
            self.status_text.set_markup('<span foreground="#00ff88" weight="bold">Connected</span>')
        else:
            self.status_icon.set_markup('<span foreground="#666666">●</span>')
            self.status_text.set_markup('<span foreground="#666666">Disconnected</span>')

    def on_destroy(self, window):
        self.stop()
        Gtk.main_quit()


if __name__ == "__main__":
    app = URnetworkGUI()
    Gtk.main()
