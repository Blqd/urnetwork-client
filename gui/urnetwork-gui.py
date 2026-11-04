#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, GLib, Gdk
import subprocess
import os
import signal
import sys
import fcntl
import time
import threading
import re
from datetime import datetime, timezone

CONFIG_DIR = os.path.expanduser("~/.config/urnetwork")
AUTH_FILE = os.path.join(CONFIG_DIR, "auth")
LOG_FILE = os.path.join(CONFIG_DIR, "diagnostic.log")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
GUI_LOCK_FILE = os.path.join(CONFIG_DIR, "gui.lock")
LOG_MAX_BYTES = 10 * 1024 * 1024

# The TUN engine needs root for the TUN device and routes, but the GUI is
# started from a desktop entry without a TTY, so sudo cannot prompt by itself.
# SUDO_ASKPASS points sudo at a helper that opens its own terminal window; it
# is only called when sudo actually needs a password.
ASKPASS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "urnetwork-askpass.sh"))
INSTALLED_ASKPASS = "/usr/local/libexec/urnetwork/urnetwork-askpass.sh"


def askpass_path():
    """The askpass helper to use: repo copy first, then the installed copy."""
    for path in (ASKPASS, INSTALLED_ASKPASS):
        if os.access(path, os.X_OK):
            return path
    return ASKPASS


# The URnetwork provider password belongs in the desktop secret store, not in a
# plaintext file. The helper below talks to the Secret Service; the auth file is
# still used for the account name and as a fallback when no keyring is running.
CREDENTIALS_HELPER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "urnetwork-credentials.py"))
INSTALLED_CREDENTIALS_HELPER = "/usr/local/libexec/urnetwork/urnetwork-credentials.py"
KEYRING_ENABLED = os.environ.get("URNETWORK_KEYRING", "1") != "0"


def credentials_helper_path():
    """The credentials helper to use: repo copy first, then the installed one."""
    override = os.environ.get("URNETWORK_CREDENTIALS_HELPER")
    if override:
        return override
    for path in (CREDENTIALS_HELPER, INSTALLED_CREDENTIALS_HELPER):
        if os.access(path, os.R_OK):
            return path
    return CREDENTIALS_HELPER


def _run_credentials_helper(*args, password=None, timeout=20):
    """Run the keyring helper; returns the CompletedProcess or None on failure."""
    path = credentials_helper_path()
    if not os.access(path, os.R_OK):
        return None
    try:
        return subprocess.run(
            ["/usr/bin/python3", path, *args],
            input=(password or None),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def keyring_available():
    if not KEYRING_ENABLED:
        return False
    result = _run_credentials_helper("available")
    return result is not None and result.returncode == 0


def keyring_get(email):
    """The stored password for `email`, or "" when there is none."""
    if not KEYRING_ENABLED or not email:
        return ""
    result = _run_credentials_helper("get", email)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip()


def keyring_set(email, password):
    if not KEYRING_ENABLED or not email or not password:
        return False
    result = _run_credentials_helper("set", email, password=password + "\n")
    return result is not None and result.returncode == 0


def keyring_delete(email):
    if not KEYRING_ENABLED or not email:
        return False
    result = _run_credentials_helper("delete", email)
    return result is not None and result.returncode == 0


def sudo_env(base=None):
    """Environment for sudo-launching children, with the askpass helper set."""
    env = dict(os.environ if base is None else base)
    path = askpass_path()
    if os.access(path, os.X_OK):
        env["SUDO_ASKPASS"] = path
        # `prefer` lets sudo use the helper whenever it has no terminal of its
        # own, but still skips it while a password is already cached.
        env["SUDO_ASKPASS_REQUIRE"] = "prefer"
        # The launcher re-execs itself through sudo; even if the GUI happened to
        # be started from a terminal, the password must be asked in its own
        # window, not on a terminal the user may not be looking at.
        env["URNETWORK_FORCE_ASKPASS"] = "1"
    return env
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
        self.log_lock = threading.Lock()
        self.log_fp = None
        self._stdout_fd = None
        self._stdout_buf = b""
        self._pending_log = []
        self._flush_id = 0
        self._stopping = False
        self._stop_deadline = 0.0
        self._kill_sent = False
        self._start_args = ([], {}, "", "")

        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        self.log_fp = self._open_log_file()
        self._write_log_line("=== GUI started ===")

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
        self.proxy_active.set_markup('<span foreground="#666666" size="small"></span>')
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
        self._update_proxy_buttons()
        self.window.show_all()
        GLib.idle_add(self.fetch_locations)

    def _write_auth_file(self, email, password, net, include_password):
        """Write the auth file atomically with 0600. Returns True on success."""
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        lines = ["USER_AUTH={}".format(email)]
        if include_password:
            lines.append("PASSWORD={}".format(password))
        else:
            lines.append("# password is stored in the system secret store")
        if net:
            lines.append("NETWORK_NAME={}".format(net))
        tmp = AUTH_FILE + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, AUTH_FILE)
        except OSError as e:
            self.log("⚠ Could not save credentials: {}".format(e))
            return False
        return True

    def load_credentials(self):
        email = ""
        file_password = ""
        net = ""
        if os.path.exists(AUTH_FILE):
            with open(AUTH_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    v = v.strip("'\"")
                    if k == "USER_AUTH":
                        email = v
                    elif k == "PASSWORD":
                        file_password = v
                    elif k == "NETWORK_NAME":
                        net = v

        password = file_password
        if email:
            if not password:
                password = keyring_get(email)
                if password:
                    self.log("🔐 Password loaded from the system secret store")
            elif not keyring_get(email):
                # One-time migration from the old plaintext file.
                if keyring_set(email, password):
                    self._write_auth_file(email, password, net, include_password=False)
                    self.log("🔐 Password moved to the system secret store")

        if email:
            self.email_entry.set_text(email)
        if password:
            self.pass_entry.set_text(password)
        if net:
            self.net_entry.set_text(net)

    def save_credentials(self):
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        email = self.email_entry.get_text().strip()
        password = self.pass_entry.get_text()
        net = self.net_entry.get_text().strip()
        for name, value in (("USER_AUTH", email), ("PASSWORD", password), ("NETWORK_NAME", net)):
            if "\n" in value or "\r" in value:
                self.log("⚠ {} must not contain line breaks".format(name))
                return
        # Prefer the desktop secret store; fall back to the 0600 file when no
        # keyring session is available (SSH, bare WM, secret service missing).
        stored = keyring_set(email, password) if password else False
        if self._write_auth_file(email, password, net, include_password=not stored):
            if stored:
                self.log("✓ Credentials saved (password in the system secret store)")
            else:
                self.log("✓ Credentials saved")

    def _open_log_file(self):
        try:
            self._rotate_log()
            fd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.chmod(LOG_FILE, 0o600)
            return os.fdopen(fd, "a", buffering=1, encoding="utf-8")
        except OSError as e:
            print("warning: cannot open log file {}: {}".format(LOG_FILE, e))
            return None

    def _rotate_log(self):
        try:
            if os.path.getsize(LOG_FILE) <= LOG_MAX_BYTES:
                return
            rotated = LOG_FILE + ".1"
            try:
                os.remove(rotated)
            except OSError:
                pass
            os.replace(LOG_FILE, rotated)
        except OSError:
            pass

    def log(self, msg):
        GLib.idle_add(self._log, msg)

    def _log(self, msg):
        self._write_log_line(msg)
        self._append_log(msg)

    def _append_log(self, msg):
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, msg + "\n")
        mark = self.log_buffer.create_mark(None, end, False)
        self.log_view.scroll_to_mark(mark, 0, True, 0, 1)
        return False

    def _write_log_line(self, msg):
        if not self.log_fp:
            return
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with self.log_lock:
            self.log_fp.write("[{}] {}\n".format(ts, msg))

    def _log_stream_line(self, prefix, line):
        text = line.rstrip()
        if not text:
            return
        # glog lines are noisy and were the main reason diagnostic.log grew to GBs:
        # keep them only in verbose mode, and always keep connection/status lines.
        show = self.verbose_log or not GLOG_RE.match(text) or text.startswith("VPN connected.")
        if not show:
            return
        self._write_log_line(prefix + text)
        self._queue_log(text)

    def _queue_log(self, msg):
        self._pending_log.append(msg)
        if len(self._pending_log) > 500:
            del self._pending_log[:-500]
        if self._flush_id == 0:
            self._flush_id = GLib.timeout_add(80, self._flush_log)

    def _flush_log(self):
        self._flush_id = 0
        if not self._pending_log:
            return False
        chunk = "\n".join(self._pending_log)
        del self._pending_log[:]
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, chunk + "\n")
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
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
                self._write_log_line("=== list-locations start ===")
                proc = subprocess.Popen(
                    [SCRIPT, "list-locations"], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True
                )
                out, _ = proc.communicate(timeout=30)
                self._write_log_line("=== list-locations exit rc={} ===".format(proc.returncode))
                for line in out.splitlines():
                    self._log_stream_line("[locations] ", line)
                countries, regions, cities = self.parse_locations(out)
                if countries or cities:
                    GLib.idle_add(self.populate_locations, countries, regions, cities)
            except Exception as e:
                self._write_log_line("list-locations error: {}".format(e))
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
            self._update_proxy_buttons()

    def _update_proxy_buttons(self):
        can_proxy = self.mode == "socks"
        try:
            self.proxy_btn.set_sensitive(can_proxy)
            self.proxy_btn.set_tooltip_text(
                "System SOCKS5 proxy is only used in SOCKS5 mode" if not can_proxy else
                "Point the desktop SOCKS5 proxy settings at the local tunnel"
            )
        except AttributeError:
            pass

    def on_toggle(self, btn):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.running or self.process:
            return
        country = self.country_combo.get_active_text() or ""
        city = self.city_combo.get_active_text() or ""
        if not country and not city:
            self.log("⚠  No exit location selected — load locations and choose Country/City first")
            return

        self.save_credentials()
        env = os.environ.copy()
        env["USER_AUTH"] = self.email_entry.get_text().strip()
        env["PASSWORD"] = self.pass_entry.get_text()
        env["URNETWORK_STATE_FILE"] = STATE_FILE
        env["URNETWORK_VERBOSE"] = "1" if self.verbose_log else "0"
        if self.mode == "vpn":
            env = sudo_env(env)

        args = [SCRIPT, self.mode]
        if country:
            args.extend(["--country", country])
        if city:
            args.extend(["--city", city])

        self._start_args = (args, env, country, city)
        if self.mode == "vpn":
            self.log("🔑  If root access is required, a separate terminal window will ask for the sudo password")
        self._start_engine()

    def _start_engine(self):
        if self.running or self.process:
            return
        args, env, country, city = self._start_args
        self.log("▶  Starting " + self.mode.upper() + "...")
        self._write_log_line("=== start mode={} country={} city={} ===".format(self.mode, country or "-", city or "-"))

        self.connected = False
        self._stopping = False
        self._kill_sent = False
        self._stdout_buf = b""
        self.set_connecting(True)
        try:
            self.process = subprocess.Popen(
                args, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True
            )
        except OSError as e:
            self.set_connecting(False)
            self.log("⚠  Could not start: {}".format(e))
            return
        self.running = True
        self.toggle_btn.set_label("⏹  Stop")
        self.toggle_btn.get_style_context().remove_class("btn-start")
        self.toggle_btn.get_style_context().add_class("btn-stop")

        self._stdout_fd = self.process.stdout.fileno()
        try:
            os.set_blocking(self._stdout_fd, False)
        except OSError:
            pass
        GLib.io_add_watch(
            self._stdout_fd, GLib.IO_IN | GLib.IO_HUP,
            self._on_stdout_readable
        )
        GLib.child_watch_add(
            self.process.pid, self.on_process_end
        )

    def stop(self):
        self.log("⏹  Stopping...")
        if self.process:
            self._stopping = True
            self._stop_deadline = time.monotonic() + 5.0
            self._kill_sent = False
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except OSError:
                pass
            GLib.timeout_add(200, self._poll_stop)
        else:
            self._on_stopped(expected=True)

    def _poll_stop(self):
        if not self.process:
            self._on_stopped(expected=True)
            return False
        try:
            self.process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            if not self._kill_sent and time.monotonic() >= self._stop_deadline:
                self._kill_sent = True
                self.log("⚠  Engine did not stop after SIGTERM, sending SIGKILL")
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except OSError:
                    pass
            return True
        except OSError:
            pass
        self.process = None
        self._on_stopped(expected=True)
        return False

    def _on_stopped(self, expected, status=None):
        if not self.running:
            return
        was_connected = self.connected
        self.running = False
        self.connected = False
        self._stopping = False
        self._stdout_fd = None
        self._stdout_buf = b""
        self.toggle_btn.set_label("▶  Start")
        self.toggle_btn.get_style_context().remove_class("btn-stop")
        self.toggle_btn.get_style_context().add_class("btn-start")
        self.set_connected(False)
        self._write_log_line(
            "=== engine stopped status={} connected={} expected={} ===".format(
                status, was_connected, expected))
        if expected:
            self.log("✓ Stopped")
        else:
            self.log("⚠  Engine exited unexpectedly (status {})".format(status))
        # Always clean up: a crashed engine may leave the TUN device and the
        # default route behind, which takes the whole machine offline.
        threading.Thread(target=self._cleanup_engine, daemon=True).start()

    def _cleanup_engine(self):
        try:
            # `off` needs root to remove the TUN device and restore the route.
            # sudo askpass opens a terminal window only if no credential is
            # cached any more (e.g. the engine crashed much later).
            proc = subprocess.run(
                [SCRIPT, "off"], capture_output=True, timeout=120,
                env=sudo_env(os.environ))
            out = (proc.stdout or b"") + (proc.stderr or b"")
            if out.strip():
                for line in out.decode("utf-8", "replace").splitlines():
                    self._write_log_line("[off] " + line)
        except Exception as e:
            self._write_log_line("cleanup off error: {}".format(e))

    def _on_stdout_readable(self, fd, condition):
        if condition & GLib.IO_HUP:
            return False
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return True
        except OSError:
            return False
        if not data:
            return False
        self._stdout_buf += data
        while b"\n" in self._stdout_buf:
            raw, self._stdout_buf = self._stdout_buf.split(b"\n", 1)
            self.handle_line(raw.decode("utf-8", "replace"))
        return True

    def handle_line(self, text):
        if self._is_connected_output(text):
            if not self.connected:
                self.connected = True
                GLib.idle_add(self.set_connected, True)
        self._log_stream_line("[proc] ", text)

    def on_output(self, source, condition=None):
        """Line-source compatible entry point (also used by the tests)."""
        if condition == GLib.IO_HUP and not hasattr(source, "read"):
            return False
        try:
            data = source.read()
        except Exception:
            return False
        if not data:
            return False
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        for line in data.splitlines():
            self.handle_line(line)
        return True

    def _is_connected_output(self, text):
        if text.startswith("VPN connected."):
            return True
        return self.mode == "socks" and text.startswith("socks5 server is listening on ")

    def on_process_end(self, pid, status):
        expected = self._stopping
        self.process = None
        self._write_log_line("=== process exit pid={} status={} ===".format(pid, status))
        self._on_stopped(expected=expected, status=status)

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
        if self.mode != "socks":
            self.log("⚠  The system proxy is only used in SOCKS5 mode; switch mode first")
            return
        if self.set_system_proxy(True):
            self.proxy_active.set_markup(
                '<span foreground="#00ff88" size="small">● SOCKS5 127.0.0.1:9999</span>'
            )
            self.log("✓ System proxy set to SOCKS5 127.0.0.1:9999")

    def on_proxy_clear(self, btn):
        if self.set_system_proxy(False):
            self.proxy_active.set_markup(
                '<span foreground="#666666" size="small"></span>'
            )
            self.log("✓ System proxy cleared")

    def on_delete(self, window, event):
        self.save_credentials()
        return False

    def on_destroy(self, window):
        # Give the engine a short chance to shut down cleanly from here, then
        # leave the detailed cleanup to the background `off` runner; never
        # block the GTK main loop waiting for sudo.
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except OSError:
                pass
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                try:
                    if self.process.poll() is not None:
                        break
                except OSError:
                    break
                time.sleep(0.05)
        if self._pending_log:
            self._write_log_line("\n".join(self._pending_log))
            del self._pending_log[:]
        self._write_log_line("=== GUI stopped ===")
        if self.log_fp:
            try:
                self.log_fp.close()
            except OSError:
                pass
            self.log_fp = None
        Gtk.main_quit()


def acquire_single_instance_lock():
    """Avoid two GUIs fighting over the same engine/TUN device."""
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd = os.open(GUI_LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode("ascii"))
    return fd


if __name__ == "__main__":
    lock_fd = acquire_single_instance_lock()
    if lock_fd is None:
        print("URnetwork GUI is already running")
        sys.exit(0)
    app = URnetworkGUI()
    Gtk.main()
