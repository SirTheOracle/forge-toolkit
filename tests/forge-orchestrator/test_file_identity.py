"""Identity hashing must not cap native executable size; content reads stay capped."""

import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from host_fixture import host


class FileIdentityTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))

    def sparse(self, name, size, tail=b"tail"):
        path = self.dir / name
        with open(path, "wb") as stream:
            stream.truncate(size - len(tail))
            stream.seek(size - len(tail))
            stream.write(tail)
        return path

    def test_identity_hashes_executable_larger_than_content_cap(self):
        # codex-cli 0.171.0 ships a ~298 MB binary; identity must still resolve.
        size = 256 * 1024 * 1024 + 4096
        path = self.sparse("codex", size)
        expected = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                expected.update(chunk)
        self.assertEqual(host.file_identity(path), {"path": str(path), "sha256": expected.hexdigest()})
        with self.assertRaises(host.Refusal):
            host.file_bytes(path)

    def test_identity_matches_content_hash_for_small_files(self):
        path = self.dir / "small"
        path.write_bytes(b"forge\n")
        self.assertEqual(host.file_identity(path)["sha256"], hashlib.sha256(b"forge\n").hexdigest())

    def test_identity_keeps_unsafe_input_refusals(self):
        target = self.dir / "target"
        target.write_bytes(b"x")
        link = self.dir / "link"
        link.symlink_to(target)
        hard = self.dir / "hard"
        os.link(target, hard)
        with self.assertRaises((host.Refusal, OSError)):  # O_NOFOLLOW refuses at open
            host.file_identity(link)
        for path in (hard, self.dir):
            with self.assertRaises(host.Refusal):
                host.file_identity(path)
        with self.assertRaises(host.Refusal):
            host.file_identity(Path("relative"))

    def test_unknown_kind_still_refused(self):
        path = self.dir / "small"
        path.write_bytes(b"x")
        with self.assertRaises(host.Refusal):
            host.file_identity(path, "not-a-kind")


if __name__ == "__main__":
    unittest.main()
