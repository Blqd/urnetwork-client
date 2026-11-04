import os
import subprocess
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAUNCHER = os.path.join(ROOT, "scripts", "codex-tun.sh")


class CodexTunLauncherTest(unittest.TestCase):
    def test_launcher_removes_proxy_environment(self):
        env = os.environ.copy()
        env.update(
            {
                "CODEX_BIN": "env",
                "HTTP_PROXY": "http://127.0.0.1:10809/",
                "HTTPS_PROXY": "http://127.0.0.1:10809/",
                "ALL_PROXY": "socks://127.0.0.1:9999/",
                "NO_PROXY": "",
                "http_proxy": "http://127.0.0.1:10809/",
                "https_proxy": "http://127.0.0.1:10809/",
                "all_proxy": "socks://127.0.0.1:9999/",
                "no_proxy": "",
            }
        )

        proc = subprocess.run([LAUNCHER], env=env, text=True, capture_output=True, check=True)

        names = {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
        present = {line.split("=", 1)[0] for line in proc.stdout.splitlines() if "=" in line}
        self.assertFalse(names & present)


if __name__ == "__main__":
    unittest.main()
