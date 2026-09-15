from pathlib import Path
import tomllib
import unittest

import native_probe


class WorkerProfileTests(unittest.TestCase):
    def test_default_keeps_containment_and_disables_ambient_hooks(self):
        argv = native_probe.native_worker_argv("codex",Path("/fixture"),"task")
        self.assertEqual(argv[argv.index("--ask-for-approval")+1],"never")
        self.assertEqual(argv[argv.index("--sandbox")+1],"workspace-write")
        self.assertIn("sandbox_workspace_write.network_access=false",argv)
        self.assertIn("sandbox_workspace_write.writable_roots=[]",argv)
        self.assertEqual(argv[-7:],["--disable","hooks","--disable","plugins","exec","--json","task"])

    def test_pointer_candidate_uses_exact_read_only_rules_without_legacy_flags(self):
        paths = ["/fixture/with spaces/.git",'/fixture/quote"dir/.git',"/fixture/with spaces/.git"]
        argv = native_probe.native_worker_argv("codex","/fixture","task",paths)
        self.assertNotIn("--sandbox",argv)
        self.assertFalse(any(a.startswith("sandbox_workspace_write.") for a in argv))
        value = next(a for a in argv if a.startswith("permissions.p1_nested="))
        profile = tomllib.loads(value)["permissions"]["p1_nested"]
        self.assertEqual(profile,{"extends":":workspace","filesystem":dict.fromkeys(paths,"read"),
                                  "network":{"enabled":False}})
        self.assertIn('default_permissions="p1_nested"',argv)
        self.assertEqual(argv[argv.index("--ask-for-approval")+1],"never")

    def test_invalid_or_empty_pointer_inventory_is_refused(self):
        for paths in ([],["relative/.git"],["/fixture/config"],["/fixture/../other/.git"]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                native_probe.native_worker_argv("codex","/fixture","task",paths)
