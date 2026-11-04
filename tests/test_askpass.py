#!/usr/bin/env python3
"""Tests for scripts/urnetwork-askpass.sh (the SUDO_ASKPASS helper)."""
import os
import subprocess
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASKPASS = os.path.join(PROJECT_DIR, "scripts", "urnetwork-askpass.sh")


TERMINALS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "mate-terminal", "tilix", "kitty", "alacritty", "xterm",
]


def _fake_terminal(tmpdir):
    """Stand-ins for every terminal emulator the helper may pick up.

    Stubs must shadow the real binaries on PATH, otherwise a test run on a
    desktop machine would open actual windows and wait for a human.
    """
    # A real terminal emulator gives its child a fresh pty.  A background job
    # in a non-interactive shell, however, starts with stdin=/dev/null, so a
    # stub that merely `exec`s the command would read EOF instead of a
    # password.  The stub therefore plays the user: it writes the password
    # into the file the helper passed as the last argument.
    # Note: an apostrophe must not sit inside ${...:-...}, it confuses the
    # parser; put the literal password in a plain double-quoted string.
    stub = (
        "#!/usr/bin/env bash\n"
        'for arg in "$@"; do passfile="$arg"; done\n'
        "printf '%s' \"p@ss word's\" > \"$passfile\"\n"
    )
    for name in TERMINALS:
        path = os.path.join(tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(stub)
        os.chmod(path, 0o755)
    return tmpdir


def test_askpass_returns_typed_password():
    """The helper must print exactly the password typed in the terminal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _fake_terminal(tmpdir)
        env = dict(os.environ)
        env["PATH"] = tmpdir + os.pathsep + env.get("PATH", "")
        env["DISPLAY"] = ":0"
        # The fake terminal inherits stdin, so feed the "typed" password.
        proc = subprocess.run(
            ["/bin/bash", ASKPASS, "Пароль sudo: "],
            input=b"p@ss word's\n",
            capture_output=True,
            env=env,
            timeout=30,
        )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b"p@ss word's\n"


def test_askpass_fails_without_display():
    """No graphical session means no terminal window: fail, do not hang."""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    proc = subprocess.run(
        ["/bin/bash", ASKPASS, "prompt"],
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode != 0
    assert b"no graphical session" in proc.stderr


def test_askpass_fails_without_terminal_emulator():
    """A bare PATH has no terminal emulator: report it instead of hanging."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # PATH with the coreutils the helper needs, but no terminal emulator.
        for name in ("mktemp", "chmod", "rm", "date", "sleep", "cat"):
            src = "/usr/bin/" + name if os.path.exists("/usr/bin/" + name) else "/bin/" + name
            os.symlink(src, os.path.join(tmpdir, name))
        env = dict(os.environ)
        env["PATH"] = tmpdir
        env["DISPLAY"] = ":0"
        env["URNETWORK_ASKPASS_TIMEOUT"] = "5"
        proc = subprocess.run(
            ["/bin/bash", ASKPASS, "prompt"],
            input=b"whatever\n",
            capture_output=True,
            env=env,
            timeout=30,
        )
    assert proc.returncode != 0
    assert b"no terminal emulator found" in proc.stderr
