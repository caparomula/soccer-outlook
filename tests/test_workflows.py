"""Publication must distinguish a quiet schedule from damaged build output."""
import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_build", ROOT / ".github/scripts/check-build.py")
check_build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_build)


class Publication(unittest.TestCase):
    @staticmethod
    def page(count):
        return ('<!doctype html><html><body><main id="app">'
                + '<li class="row avail"></li>' * count + '</main></body></html>')

    @staticmethod
    def report(count, **changes):
        return dict({"version": 1, "publishable": True, "complete": True, "fixtures": count}, **changes)

    def test_quiet_and_empty_complete_schedules_can_publish(self):
        for count in (0, 1, 12, 100):
            with self.subTest(count=count):
                self.assertEqual(check_build.check(self.page(count), self.report(count)), [])

    def test_builder_rejection_and_missing_reports_stop_publication(self):
        for report in (None, {}, {"version": 2}, self.report(4, publishable=False, reasons=["No usable sources."])):
            with self.subTest(report=report):
                self.assertTrue(check_build.check(self.page(4), report))
        self.assertIn("No usable sources.", check_build.check(self.page(4),
                      self.report(4, publishable=False, reasons=["No usable sources."])))

    def test_mismatched_counts_unfinished_documents_and_template_markers_stop_publication(self):
        for html in (self.page(3), self.page(4).replace('</html>', ''),
                     self.page(4).replace('id="app"', 'id="something-else"'),
                     self.page(4).replace('</main>', '@@SCRIPT@@</main>')):
            with self.subTest(html=html):
                self.assertTrue(check_build.check(html, self.report(4)))

    def test_invalid_fixture_counts_stop_publication(self):
        for count in (None, "0", False, -1):
            self.assertTrue(check_build.check(self.page(0), self.report(count)))

    def test_the_command_rejects_absent_or_malformed_reports(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            page, report = Path(folder) / "index.html", Path(folder) / "report.json"
            page.write_text(self.page(0), encoding="utf-8")
            args = ["--page", str(page), "--report", str(report)]
            self.assertEqual(check_build.main(args), 1)
            report.write_text("{unfinished", encoding="utf-8")
            self.assertEqual(check_build.main(args), 1)
            report.write_text(json.dumps(self.report(0)), encoding="utf-8")
            self.assertEqual(check_build.main(args), 0)
