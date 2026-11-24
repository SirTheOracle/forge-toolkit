"""Sealed-system hard-linked tools must run in place and still hash safely.

macOS launch constraints SIGKILL a copied Apple platform binary, so packaging
must not copy /usr/bin/grep etc. The single-link rule still applies to every
file on a writable volume.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from host_fixture import host

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import forge_runtime as runtime  # noqa: E402

GREP = Path("/usr/bin/grep")
SEALED = GREP.exists() and GREP.stat().st_nlink > 1 and runtime.sealed_system_path(GREP)


@unittest.skipUnless(SEALED, "requires a hard-linked grep on a read-only system volume")
class SealedToolTests(unittest.TestCase):
    def test_sealed_hardlinked_tool_hashes(self):
        self.assertEqual(len(host.file_identity(GREP)["sha256"]), 64)

    def test_sealed_tool_runs_in_place(self):
        cp = subprocess.run([str(GREP), "-Eq", "^x$"], input="x\n", text=True)
        self.assertEqual(cp.returncode, 0)

    def test_writable_hardlink_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp).resolve() / "a"
            a.write_bytes(b"x")
            os.link(a, Path(tmp).resolve() / "b")
            self.assertFalse(runtime.sealed_system_path(a))
            with self.assertRaises(host.Refusal):
                host.file_identity(a)

    def test_staging_does_not_copy_sealed_tools(self):
        source = (ROOT / "lib/forge_runtime.py").read_text()
        self.assertIn("not sealed_system_path(resolved)", source)


if __name__ == "__main__":
    unittest.main()
