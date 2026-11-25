import unittest
from unittest.mock import Mock

from host_probe import Fixture


class BindingTests(unittest.TestCase):
    def fixture(self, output):
        fixture = Fixture.__new__(Fixture)
        fixture.socket = "forge-p1-test"
        fixture.sessions = {"primary": {"name": "p1-test-primary"}}
        fixture.call = Mock(return_value={"stdout": output})
        return fixture

    def test_binding_uses_numeric_session_id_and_actual_pane_id(self):
        fixture = self.fixture("/private/tmp/tmux-501/forge-p1-test|123|$0|%2|p1-test-primary\n")
        self.assertEqual(fixture.binding("primary", 3), {
            "TMUX": "/private/tmp/tmux-501/forge-p1-test,123,0", "TMUX_PANE": "%2",
            "TMUX_SESSION": "p1-test-primary"})
        self.assertIn("p1-test-primary:.3", fixture.call.call_args.args)

    def test_binding_refuses_foreign_or_malformed_observations(self):
        valid = "/private/tmp/tmux-501/forge-p1-test|123|$0|%2|p1-test-primary"
        for output in ("", valid.replace("forge-p1-test", "default"),
                       valid.replace("|123|", "|0|"), valid.replace("|$0|", "|0|"),
                       valid.replace("|%2|", "|2|"), valid.replace("|p1-test-primary", "|foreign"),
                       valid.lstrip("/")):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.fixture(output).binding("primary", 3)

    def test_binding_refuses_non_fixture_indices_before_tmux(self):
        fixture = self.fixture("")
        for index in (-1, 5, "3", True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                fixture.binding("primary", index)
        fixture.call.assert_not_called()
