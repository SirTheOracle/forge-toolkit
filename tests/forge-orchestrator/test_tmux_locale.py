"""Tab-separated tmux formats must survive the packaged LC_ALL=C environment.

tmux >= 3.6 rewrites non-printable characters (including tab) in -F output to "_"
when the client locale is not UTF-8, so every reader that splits on tab must pass -u.
"""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TMUX = shutil.which("tmux")
READERS = ["bin/forge", "bin/forge-bridge", "bin/forge-cc-hook", "bin/forge-watch", "bin/forge-orchestrator-host"]


class TmuxLocaleTests(unittest.TestCase):
    @unittest.skipUnless(TMUX, "tmux unavailable")
    def test_dash_u_preserves_tabs_under_c_locale(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = str(Path(tmp) / "s")
            env = {"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin", "TERM": "xterm-256color"}
            subprocess.run([TMUX, "-S", sock, "new-session", "-d", "-s", "locale-probe"], env=env, check=True)
            try:
                out = subprocess.run([TMUX, "-u", "-S", sock, "list-sessions", "-F",
                                      "#{session_name}\t#{session_id}"], env=env, check=True,
                                     capture_output=True, text=True).stdout.strip()
            finally:
                subprocess.run([TMUX, "-S", sock, "kill-server"], env=env, capture_output=True)
        self.assertEqual(out.split("\t")[0], "locale-probe")
        self.assertEqual(len(out.split("\t")), 2)

    def test_every_tab_format_reader_passes_dash_u(self):
        # A tmux argv whose format contains a tab must carry -u right after the binary.
        missing = []
        for rel in READERS:
            text = (ROOT / rel).read_text()
            for match in re.finditer(r"""\[\s*["']tmux["'](.{0,200}?)\]""", text, re.S):
                if "\\t" in match.group(1) and not re.match(r"""\s*,\s*["']-u["']""", match.group(1)):
                    missing.append(rel + ": " + match.group(0)[:80])
        self.assertEqual(missing, [])
        host = (ROOT / "bin/forge-orchestrator-host").read_text()
        self.assertIn('argv = [self.binary, "-u"]', host)


if __name__ == "__main__":
    unittest.main()
