import os
import subprocess
import unittest
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HELPER = os.path.join(ROOT, "scripts", "urnetwork-credentials.py")


def run_helper(*args, password=None):
    return subprocess.run(
        ["/usr/bin/python3", HELPER, *args],
        input=password,
        capture_output=True,
        text=True,
        timeout=60,
    )


@unittest.skipUnless(os.access(HELPER, os.R_OK), "credentials helper is missing")
class CredentialsHelperTest(unittest.TestCase):
    def setUp(self):
        available = run_helper("available")
        if available.returncode != 0:
            self.skipTest("secret service not available: " + available.stderr.strip())
        self.email = "urnetwork-test-%s@example.com" % uuid.uuid4().hex

    def tearDown(self):
        run_helper("delete", self.email)

    def test_roundtrip(self):
        stored = run_helper("set", self.email, password="p@ss word's\n")
        self.assertEqual(stored.returncode, 0, stored.stderr)
        loaded = run_helper("get", self.email)
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        self.assertEqual(loaded.stdout, "p@ss word's")

    def test_missing_entry_is_not_an_error_for_output(self):
        missing = run_helper("get", self.email)
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stdout, "")

    def test_empty_password_is_rejected(self):
        rejected = run_helper("set", self.email, password="\n")
        self.assertEqual(rejected.returncode, 2)

    def test_delete_removes_the_entry(self):
        run_helper("set", self.email, password="secret\n")
        self.assertEqual(run_helper("delete", self.email).returncode, 0)
        self.assertEqual(run_helper("get", self.email).returncode, 1)


if __name__ == "__main__":
    unittest.main()
