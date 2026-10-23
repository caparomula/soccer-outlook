"""Enforce the P0 core's deliberately small dependency and clock boundary."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CompanionCoreBoundary(unittest.TestCase):
    def test_source_decoder_has_no_transport_storage_or_clock(self):
        paths = [ROOT / "packages/outlook_data/lib/espn_decoder.dart",
                 ROOT / "packages/outlook_data/lib/src/espn_decoder.dart"]
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text()
                for uri in re.findall(r"""(?:import|export)\s+['"]([^'"]+)['"]""", source):
                    if ":" in uri:
                        self.assertIn(uri, {"dart:core", "dart:collection", "dart:convert", "dart:math"})
                    else:
                        self.assertIn((path.parent / uri).resolve(), paths)
                self.assertNotRegex(source, r"\b(?:TZDateTime|DateTime)\s*\.\s*now\s*\(")

    def test_domain_has_no_platform_io_or_ambient_clock(self):
        allowed = {
            "dart:core", "dart:collection", "dart:convert", "dart:math", "dart:typed_data",
            "package:timezone/timezone.dart", "package:timezone/data/latest_all.dart",
        }
        sources = list((ROOT / "packages/outlook_core/lib").rglob("*.dart"))
        self.assertTrue(sources, "No domain sources checked")
        for path in sources:
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text()
                for uri in re.findall(r"""(?:import|export)\s+['"]([^'"]+)['"]""", source):
                    if ":" in uri:
                        self.assertIn(uri, allowed, "Review any new core dependency")
                    else:
                        target = (path.parent / uri).resolve()
                        self.assertTrue(target.is_relative_to(ROOT / "packages/outlook_core/lib"))
                self.assertNotRegex(source, r"\b(?:TZDateTime|DateTime)\s*\.\s*now\s*\(")


if __name__ == "__main__":
    unittest.main()
