"""Package verification must describe the packaged interpreter, not the checking one.

forge-start and bin/forge invoke the host via /usr/bin/python3 -I, while staging may
run under a different python3 on PATH. NATIVE_STARTUP_CHANGED must not fire merely
because the checking interpreter differs from the staging interpreter.
"""

import json
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
import forge_runtime as runtime  # noqa: E402

SYSTEM_PYTHON = Path("/usr/bin/python3")


class ImplicitInputsTests(unittest.TestCase):
    def test_default_matches_running_stdlib(self):
        self.assertEqual(runtime.implicit_inputs(), runtime.implicit_inputs(sysconfig.get_path("stdlib")))

    def test_archive_name_follows_given_stdlib_not_running_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdlib = Path(tmp).resolve() / "lib" / "python3.7"
            stdlib.mkdir(parents=True)
            paths = {item["path"] for item in runtime.implicit_inputs(stdlib)}
        self.assertIn(str(stdlib.parent / "python37.zip"), paths)

    def test_unrecognized_stdlib_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                runtime.implicit_inputs(tmp)

    @unittest.skipUnless(SYSTEM_PYTHON.exists(), "system python3 unavailable")
    def test_other_interpreter_reproduces_recorded_inputs(self):
        recorded = runtime.implicit_inputs()
        stdlib = sysconfig.get_path("stdlib")
        code = ("import json,sys; sys.path.insert(0, sys.argv[1]); import forge_runtime as r; "
                "print(json.dumps(r.implicit_inputs(sys.argv[2])))")
        out = subprocess.check_output([str(SYSTEM_PYTHON), "-I", "-c", code, str(ROOT / "lib"), stdlib], text=True)
        self.assertEqual(json.loads(out), recorded)


if __name__ == "__main__":
    unittest.main()
