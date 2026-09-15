"""Storage/CAS tests; these do not qualify writer authority or native policy."""

import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p2_records", ROOT / "lib/forge_orchestrator.py")
records = importlib.util.module_from_spec(spec)
spec.loader.exec_module(records)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge-p2-records-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        root, git = str(self.base / "project"), str(self.base / "git")
        self.root = dict(physical_root=root, git_dir=git, git_common_dir=git,
                         root_id=hashlib.sha256((root + "\0" + git).encode()).hexdigest())
        self.protected = Path(git) / "forge/broker-v1/roots" / self.root["root_id"]
        self.store = records.Store(self.protected, self.root)
        self.process = dict(pid=123, started="start1", executable="/bin/sh", sha256="1" * 64, version="fixture/1")
        self.launch = dict(schema="forge-orchestrator-launch/1", root=self.root,
                           launch_id=uuid.uuid4().hex, revision=1, state="preparing",
                           seat=dict(role="orchestrator", provider="claude", session="fixture", incarnation="100",
                                     session_id="$1", pane_id="%1", socket="/tmp/fixture"),
                           owner=self.process, profile_hash="2" * 64, package_hash="3" * 64,
                           created_at=records.now(), updated_at=records.now(), process_epoch=0,
                           inputs={name: dict(path="/protected/" + name, sha256="3" * 64)
                                   for name in ("package", "core", "adapter", "helpers")},
                           evidence=dict(reports=["4" * 64], loading="behavioral", native_policy="pass", workflows=["build"]))
        self.broker = dict(process=dict(self.process, pid=456), runtime_sha256="4" * 64,
                           protocol="forge-broker-control/1")

    def ready(self):
        self.store.reserve(self.launch, 0)
        return self.store.transition(self.launch["launch_id"], 1, 1, "ready",
                                     process=self.process, broker=self.broker, process_epoch=1)

    def test_absent_inspect_never_creates_state(self):
        self.assertIsNone(self.store.current())
        self.assertFalse(self.protected.exists())

    def test_preparing_then_ready_has_separate_revision_and_generation(self):
        ready = self.ready()
        self.assertEqual(ready["revision"], 2)
        current = self.store.current()
        self.assertEqual(current["reference"], {"launch_id": self.launch["launch_id"], "generation": 1})
        self.assertEqual(current["record"], ready)

    def test_ready_requires_observed_provider_and_broker(self):
        self.store.reserve(self.launch, 0)
        for observed in ({}, {"process": self.process}, {"broker": self.broker},
                         {"process": dict(self.process, started=""), "broker": self.broker}):
            with self.subTest(observed=observed), self.assertRaises(records.Refusal):
                self.store.transition(self.launch["launch_id"], 1, 1, "ready", **observed)
        self.assertEqual(self.store.current()["record"]["state"], "preparing")

    def test_stale_record_reference_and_launch_cas_refuse(self):
        self.store.reserve(self.launch, 0)
        for launch, revision, generation in ((uuid.uuid4().hex, 1, 1),
                                             (self.launch["launch_id"], 0, 1),
                                             (self.launch["launch_id"], 1, 0)):
            with self.subTest(revision=revision, generation=generation), self.assertRaises(records.Refusal):
                self.store.transition(launch, revision, generation, "failed", diagnostic="test")
        self.assertEqual(self.store.current()["record"], self.launch)

    def test_fixed_binding_is_not_mutable(self):
        self.store.reserve(self.launch, 0)
        for field, value in (("profile_hash", "5" * 64), ("package_hash", "5" * 64),
                             ("seat", dict(self.launch["seat"], provider="codex")), ("root", {})):
            with self.subTest(field=field), self.assertRaises(records.Refusal) as raised:
                self.store.transition(self.launch["launch_id"], 1, 1, "failed", **{field: value})
            self.assertEqual(raised.exception.code, "FIXED_BINDING")

    def test_terminal_record_is_never_reused(self):
        self.store.reserve(self.launch, 0)
        self.store.transition(self.launch["launch_id"], 1, 1, "failed", diagnostic="observation failed")
        with self.assertRaises(records.Refusal):
            self.store.transition(self.launch["launch_id"], 2, 1, "ready", process=self.process, broker=self.broker)
        with self.assertRaises(records.Refusal) as raised:
            self.store.reserve(dict(self.launch, launch_id=uuid.uuid4().hex), 1)
        self.assertEqual(raised.exception.code, "HANDOFF_REQUIRED")

    def test_competing_reservation_does_not_replace_winner(self):
        self.store.reserve(self.launch, 0)
        with self.assertRaises(records.Refusal) as raised:
            self.store.reserve(dict(self.launch, launch_id=uuid.uuid4().hex), 0)
        self.assertEqual(raised.exception.code, "REFERENCE_CONFLICT")
        self.assertEqual(self.store.current()["record"], self.launch)

    def test_crash_before_reference_leaves_unselected_record_and_refuses_retry(self):
        original = records.write_at
        def crash(fd, name, value, **kwargs):
            if name == "current.json":
                raise OSError("crash before reference")
            return original(fd, name, value, **kwargs)
        with patch.object(records, "write_at", side_effect=crash), self.assertRaises(OSError):
            self.store.reserve(self.launch, 0)
        with self.assertRaises(records.Refusal) as raised:
            self.store.current()
        self.assertEqual(raised.exception.code, "UNSELECTED_LAUNCH")
        self.assertTrue((self.store.path / "launches" / (self.launch["launch_id"] + ".json")).is_file())
        with self.assertRaises(records.Refusal) as raised:
            self.store.reserve(dict(self.launch, launch_id=uuid.uuid4().hex), 0)
        self.assertEqual(raised.exception.code, "UNSELECTED_LAUNCH")

    def test_corrupt_reference_is_not_reconstructed(self):
        self.store.reserve(self.launch, 0)
        (self.store.path / "current.json").write_text('{"generation": 1}')
        with self.assertRaises(records.Refusal):
            self.store.current()

    def test_record_wrong_root_refuses(self):
        wrong = dict(self.launch, root=dict(self.root, physical_root="/other"))
        with self.assertRaises(records.Refusal):
            self.store.reserve(wrong, 0)
        self.assertFalse(self.store.path.exists())

    def test_live_comparison_cannot_route_from_record(self):
        ready = self.ready()
        observed = dict(root=self.root, seat=self.launch["seat"], process=self.process,
                        broker=self.broker, profile_hash="2" * 64, package_hash="3" * 64)
        self.assertEqual(records.compare_live(ready, **observed), ready)
        for field, value in (("root", dict(self.root, physical_root="/other")),
                             ("seat", dict(self.launch["seat"], incarnation="101")),
                             ("seat", dict(self.launch["seat"], pane_id="%2")),
                             ("seat", dict(self.launch["seat"], provider="codex")),
                             ("process", dict(self.process, started="pid-reused")),
                             ("broker", dict(self.broker, runtime_sha256="5" * 64)),
                             ("profile_hash", "5" * 64), ("package_hash", "5" * 64)):
            with self.subTest(field=field), self.assertRaises(records.Refusal):
                records.compare_live(ready, **dict(observed, **{field: value}))

    def test_symlink_and_hardlink_records_refuse(self):
        self.store.reserve(self.launch, 0)
        current = self.store.path / "current.json"
        target = self.base / "target"
        current.rename(target)
        current.symlink_to(target)
        with self.assertRaises(OSError):
            self.store.current()
        current.unlink()
        os.link(target, current)
        with self.assertRaises(records.Refusal):
            self.store.current()

    def test_symlink_directory_refuses_without_writing_target(self):
        self.store.path.parent.mkdir(parents=True)
        target = self.base / "elsewhere"
        target.mkdir()
        self.store.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.store.reserve(self.launch, 0)
        self.assertEqual(list(target.iterdir()), [])

    def test_write_failure_cleans_temporary_file(self):
        fd = records.directory(self.base)
        try:
            with patch.object(records.os, "write", side_effect=OSError("full disk")), self.assertRaises(OSError):
                records.write_at(fd, "record.json", {})
            self.assertEqual(os.listdir(fd), [])
        finally:
            os.close(fd)

    def test_component_api_rejects_path_escape(self):
        fd = records.directory(self.base)
        try:
            for name in ("../record", "/record", ".", "", "a/b", "a\0b"):
                with self.subTest(name=name), self.assertRaises(records.Refusal):
                    records.write_at(fd, name, {})
        finally:
            os.close(fd)

    def test_duplicate_json_fields_and_nonfinite_numbers_refuse(self):
        fd = records.directory(self.base)
        try:
            for content in ('{"revision":1,"revision":2}', '{"revision":NaN}'):
                (self.base / "record.json").write_text(content)
                with self.subTest(content=content), self.assertRaises(records.Refusal):
                    records.read_at(fd, "record.json")
        finally:
            os.close(fd)

    def test_malformed_discriminants_refuse_without_type_errors(self):
        for value in (dict(self.launch, state=[]), dict(self.launch, seat=dict(self.launch["seat"], provider=[])),
                      dict(self.launch, evidence=dict(self.launch["evidence"], loading=[]))):
            with self.subTest(value=value), self.assertRaises(records.Refusal):
                records.validate_launch(value, self.root)


if __name__ == "__main__":
    unittest.main()
