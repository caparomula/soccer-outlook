#!/usr/bin/env python3
"""Check a generated page against the builder's source report before publication.

A small or empty schedule can be correct. The builder decides whether its ESPN
responses are usable; this check catches missing reports and incomplete output.
"""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = 0
        self.app = False
        self.closed = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and "row" in (attrs.get("class") or "").split():
            self.rows += 1
        if attrs.get("id") == "app":
            self.app = True

    def handle_endtag(self, tag):
        self.closed.add(tag)


def check(page, report):
    """Return publication errors, including the builder's reasons for rejecting its sources."""
    if not isinstance(report, dict) or report.get("version") != 1:
        return ["Missing or unsupported build report."]
    if report.get("publishable") is not True:
        reasons = report.get("reasons")
        return ["The builder did not approve publication."] + ([str(r) for r in reasons] if isinstance(reasons, list) else [])
    expected = report.get("fixtures")
    if type(expected) is not int or expected < 0:
        return ["The build report has no valid fixture count."]
    parsed = Page()
    parsed.feed(page)
    parsed.close()
    problems = []
    if parsed.rows != expected:
        problems.append(f"The report contains {expected} fixtures but the page contains {parsed.rows} match rows.")
    if not parsed.app or not {"body", "html"}.issubset(parsed.closed):
        problems.append("The page is missing its app or closing document markup.")
    if re.search(r"@@[A-Z_]+@@", page):
        problems.append("The page still contains an unresolved template marker.")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        page = args.page.read_text(encoding="utf-8")
        problems = check(page, report)
    except (OSError, ValueError) as error:
        problems = [f"Cannot validate the generated page: {error}"]
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"Page verified: {report['fixtures']} matches; sources "
          f"{'complete' if report.get('complete') else 'partially available, accepted by the builder'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
