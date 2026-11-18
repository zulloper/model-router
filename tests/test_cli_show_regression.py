"""Regression coverage for CLI status without gateway session state."""
from argparse import Namespace
from contextlib import redirect_stdout
import importlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PKG = "model_router_plugin"
if PKG not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
cli = importlib.import_module(f"{PKG}.cli")


class CliShowRegression(unittest.TestCase):
    def test_show_reports_off_without_live_session(self):
        values = {"mode": "off", "scope": {}, "security_sanitize": False}
        ctx = Namespace(get_config=lambda key, default=None: values.get(key, default))
        output = io.StringIO()
        with patch.object(cli, "_config", return_value={}), redirect_stdout(output):
            self.assertEqual(cli.make_handler(ctx)(Namespace(model_router_command="show")), 0)
        for expected in ("model-router: off", "security mode: off", "guardrail round-trip: off", "(empty)"):
            self.assertIn(expected, output.getvalue())


if __name__ == "__main__":
    unittest.main()
