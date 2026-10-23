"""Publication must distinguish a quiet schedule from damaged build output."""
import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_build", ROOT / ".github/scripts/check-build.py")
check_build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_build)


class TestDependencyPreparation(unittest.TestCase):
    """Check the repository's indented job/step layout without a YAML dependency."""

    @staticmethod
    def jobs(path):
        text = path.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
        parts = re.split(r"^  ([\w-]+):\n", text, flags=re.MULTILINE)
        for name, body in zip(parts[1::2], parts[2::2]):
            yield name, re.split(r"^      - ", body, flags=re.MULTILINE)[1:]

    @staticmethod
    def condition(step):
        value = re.search(r"^        if: (.+)$", step, re.MULTILINE)
        return value.group(1) if value else None

    @staticmethod
    def environment(command):
        # python/python3 share setup-python; a separate venv needs its own install.
        return command.rsplit("/", 1)[0] if "/" in command else "setup-python"

    def assert_prepared(self, module, requirements):
        checked = set()
        invocation = re.compile(r"([\w./-]*python3?) -m (" + module + r")(?=\s|$)")
        installation = re.compile(r"([\w./-]*(?:python3? -m pip|pip)) install ([^\n]+)")
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            for job, steps in self.jobs(path):
                for index, step in enumerate(steps):
                    for run in invocation.finditer(step):
                        with self.subTest(workflow=path.name, job=job, command=run.group(0)):
                            checked.add((path.name, job))
                            prepared = set()
                            for earlier in steps[:index] + [step[:run.start()]]:
                                condition = self.condition(earlier)
                                if condition not in (None, self.condition(step)):
                                    continue
                                self.assertNotIn("COMPANION_FEED_ENABLED", condition or "")
                                for install in installation.finditer(earlier):
                                    if self.environment(install.group(1)) != self.environment(run.group(1)):
                                        continue
                                    prepared.update(re.findall(r"(?:^|\s)-r\s+(\S+)", install.group(2)))
                            self.assertTrue(set(requirements) <= prepared,
                                            f"{run.group(0)} needs {requirements} installed first in its environment")
        return checked

    def test_unit_jobs_prepare_companion_dependencies_even_when_feed_is_disabled(self):
        self.assertEqual(self.assert_prepared("unittest", ["tests/requirements-companion.txt"]), {
            ("browser.yml", "unit"), ("refresh.yml", "refresh"),
            ("schedule.yml", "schedule"), ("companion.yml", "spike"),
        })

    def test_browser_jobs_prepare_their_own_test_environment(self):
        self.assertEqual(self.assert_prepared(r"tests\.browser(?:_schedule|_publication_capture)?", [
            "tests/requirements-browser.txt", "tests/requirements-companion.txt",
        ]), {("browser.yml", "browser"), ("refresh.yml", "refresh"),
             ("companion.yml", "spike")})


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
