"""Static checks for the shell entry points.

These guard the specific failure modes found in code review: a stray `local`
outside a function aborts the script at run time (bash -n does not catch it),
and the sudoers rule must never hand the root engine a caller-controlled path.
"""

import os
import re
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONNECT = os.path.join(ROOT, "scripts", "urnetwork-connect.sh")
INSTALLER = os.path.join(ROOT, "scripts", "install-nopasswd.sh")

FUNC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{\s*$")
LOCAL_RE = re.compile(r"^\s*local\s")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def function_bodies(text):
    """Map function name -> list of (lineno, line) using column-0 braces."""
    bodies = {}
    current = None
    for lineno, line in enumerate(text.splitlines(), 1):
        m = FUNC_RE.match(line)
        if m and current is None:
            current = m.group(1)
            bodies[current] = []
            continue
        if current is not None and line.startswith("}"):
            current = None
            continue
        if current is not None:
            bodies[current].append((lineno, line))
    return bodies


def case_branch(text, name):
    """Lines of one `case` branch (from `name)` up to its closing `;;`)."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*" + re.escape(name) + r"\)\s*$", line):
            start = i
            break
    if start is None:
        raise KeyError(name)
    base = len(lines[start]) - len(lines[start].lstrip())
    out = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped == ";;" and len(line) - len(line.lstrip()) == base:
            break
        out.append(line)
    return out


class ConnectScriptStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(CONNECT)
        cls.lines = cls.text.splitlines()
        cls.bodies = function_bodies(cls.text)

    def test_every_local_is_inside_a_function(self):
        """`local` at top level is a fatal error under `set -e`."""
        top_level = []
        inside = None
        for lineno, line in enumerate(self.lines, 1):
            if FUNC_RE.match(line) and inside is None:
                inside = lineno
                continue
            if inside is not None and line.startswith("}"):
                inside = None
                continue
            if inside is None and LOCAL_RE.match(line):
                top_level.append((lineno, line.strip()))
        self.assertEqual(top_level, [], "`local` outside a function: %r" % (top_level,))

    def test_register_branch_has_no_stray_local(self):
        body = case_branch(self.text, "register")
        offenders = [l.strip() for l in body if LOCAL_RE.match(l)]
        self.assertEqual(offenders, [])

    def test_register_restores_the_global_exit_trap(self):
        """Its own EXIT trap must chain the global cleanup, not replace it."""
        line = next(l for l in self.lines if l.strip().startswith("trap 'rm -rf \"$tmpdir\""))
        self.assertIn("cleanup_bg", line)

    def test_require_root_forwards_credentials_via_private_file(self):
        body = "\n".join(l for _, l in self.bodies["require_root"])
        self.assertIn("--creds-file", body)
        self.assertIn("umask 077", body)
        self.assertIn(".creds.", body)
        # Secrets must not be exported into the sudo environment any more.
        self.assertNotIn("export USER_AUTH", body)

    def test_engine_liveness_checks_verify_the_executable(self):
        body = "\n".join(l for _, l in self.bodies["is_running"])
        self.assertIn("pid_is_expected", body)
        self.assertNotIn("kill -0", body)
        # Broad pattern kills are gone: they could hit unrelated processes.
        self.assertNotIn("pkill -f", self.text)

    def test_off_verifies_pids_before_killing(self):
        body = "\n".join(case_branch(self.text, "off|stop"))
        self.assertIn("pid_is_expected", body)

    def test_route_journal_is_validated(self):
        body = "\n".join(l for _, l in self.bodies["restore_default_route"])
        self.assertIn("default", body)
        self.assertIn("ip link show", body)

    def test_list_locations_propagates_the_engine_exit_code(self):
        self.assertNotIn("list-locations 2>&1 || true", self.text)
        self.assertIn("list-locations 2>&1", self.text)


class InstallerStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(INSTALLER)

    def env_keep_lines(self):
        return [l for l in self.text.splitlines() if "env_keep" in l and "Defaults!" in l]

    def test_env_keep_is_narrowed_to_verbose_only(self):
        lines = self.env_keep_lines()
        self.assertEqual(len(lines), 1, lines)
        line = lines[0]
        for forbidden in ("USER_AUTH", "PASSWORD", "URNETWORK_CONFIG_DIR", "URNETWORK_STATE_FILE"):
            self.assertNotIn(forbidden, line, "sudoers must not preserve %s" % forbidden)
        self.assertIn("URNETWORK_VERBOSE", line)

    def test_root_copy_is_the_only_authorised_path(self):
        self.assertIn("NOPASSWD: $DEST", self.text)


if __name__ == "__main__":
    unittest.main()
