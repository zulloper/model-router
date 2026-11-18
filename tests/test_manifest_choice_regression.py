"""String enum choices must survive YAML parsing for the settings form."""
from pathlib import Path
import unittest
import yaml


class ManifestChoicesRegression(unittest.TestCase):
    def test_routing_modes_are_string_choices(self):
        manifest = yaml.safe_load((Path(__file__).resolve().parents[1] / "plugin.yaml").read_text())
        mode = manifest["config_schema"]["mode"]
        self.assertEqual(mode["choices"], ["auto", "off"])
        self.assertTrue(all(isinstance(value, str) for value in mode["choices"]))


if __name__ == "__main__":
    unittest.main()
