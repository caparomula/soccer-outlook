"""A malformed numeric budget must not silently disable the size gate."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("spike_verify", ROOT / "tool/outlook_bridge/verify.py")
spike = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spike)


class SpikeLimits(unittest.TestCase):
    def test_checked_in_limit_is_positive_bytes(self):
        self.assertGreater(spike.browser_size_budget(ROOT / "tool/companion_p0_limits.json"), 0)

    def test_nonfinite_noninteger_and_nonpositive_limits_reject(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "limits.json"
            for value in ("NaN", "Infinity", "-Infinity", "0", "-1", "true", '"204800"', "204800.5"):
                path.write_text('{"budgets":{"browser_core_gzip_bytes":' + value + '}}')
                with self.subTest(value=value), self.assertRaises(ValueError):
                    spike.browser_size_budget(path)


if __name__ == "__main__":
    unittest.main()
