"""Checks on what story.py keeps from the model's publish_story call, and on the week's schedule
build.py gives it for the forecast. The page shows whatever survives these, so each case is a way a
plausible answer could reach the page wrong. Run with: python3 -m unittest -v
"""
import os
import sys
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build  # noqa: E402
import story  # noqa: E402   (imports without the anthropic package, which only write_story needs)

FORECAST = {"label": "International break",
            "today": "The big European leagues are off; national teams fill the day.",
            "ahead": "The Premier League returns Saturday."}


def raw_story(**over):
    raw = {"headline": "A headline", "lede": "A lede.", "lede_sources": [],
           "notes": [{"match_id": "1", "note": "A note.", "sources": ["https://example.com/a"]}],
           "forecast": dict(FORECAST)}
    raw.update(over)
    return raw


FACTS = {"today_and_tomorrow_on_owner_services": [{"id": "1"}]}
SEEN = {story.url_key("https://example.com/a"): ("https://example.com/a", "A")}


class Forecast(unittest.TestCase):
    """The forecast travels with the story when it is whole, and the story survives without it."""

    def test_kept_whole(self):
        s = story.clean_story(raw_story(), FACTS, SEEN)
        self.assertEqual(s["forecast"], FORECAST)
        self.assertEqual(list(s["notes"]), ["1"])

    def test_missing_part_drops_the_forecast_not_the_story(self):
        for missing in ("label", "today", "ahead"):
            with self.subTest(missing=missing):
                f = dict(FORECAST)
                del f[missing]
                s = story.clean_story(raw_story(forecast=f), FACTS, SEEN)
                self.assertNotIn("forecast", s)
                self.assertEqual(list(s["notes"]), ["1"])

    def test_blank_or_wrong_type_parts(self):
        for bad in ({**FORECAST, "today": "   "}, {**FORECAST, "ahead": None}, {**FORECAST, "label": 7}, "a string", None):
            with self.subTest(bad=bad):
                self.assertIsNone(story.clean_forecast(bad))

    def test_sentence_for_a_label_is_refused(self):
        long_label = "The big European leagues are off for the international break"
        self.assertIsNone(story.clean_forecast({**FORECAST, "label": long_label}))

    def test_label_tidied(self):
        f = story.clean_forecast({**FORECAST, "label": "  Champions League\n night. "})
        self.assertEqual(f["label"], "Champions League night")

    def test_long_parts_trimmed_at_a_sentence(self):
        today = "First sentence here. " * 30
        f = story.clean_forecast({**FORECAST, "today": today})
        self.assertLessEqual(len(f["today"]), 420)
        self.assertTrue(f["today"].endswith("."))

    def test_schema_asks_for_it(self):
        schema = story.PUBLISH_TOOL["input_schema"]
        self.assertIn("forecast", schema["required"])
        self.assertEqual(set(schema["properties"]["forecast"]["required"]), {"label", "today", "ahead"})

    def test_refresh_shows_the_earlier_forecast(self):
        facts = {"built_at": "2026-10-07T16:55:00Z", "weekday": "Wednesday", "date": "2026-10-07", "owner_services": ["HBO Max"]}
        previous = {"generated_at": "2026-10-07T08:55:00Z", "headline": "h", "lede": "l", "notes": {}, "forecast": FORECAST}
        prompt = story.refresh_prompt(facts, previous, (5, 2))
        self.assertIn(FORECAST["ahead"], prompt)
        self.assertIn("what is left of the day", prompt)


def match(mid, league, et_hour, day, state="pre", status="", service="", comp=None, time_valid=True):
    utc = datetime(day.year, day.month, day.day, et_hour, 0, tzinfo=build.ET).astimezone(timezone.utc)
    return SimpleNamespace(id=mid, league=league, comp=comp or build.LEAGUES[league]["name"], utc=utc,
                           state=state, status=status, service=service, time_valid=time_valid)


class ScheduleByDay(unittest.TestCase):
    """The week's digest the forecast is written from: counts the model can't get wrong by sampling."""

    DAY = date(2026, 10, 7)

    def test_counts_and_first_kickoff(self):
        ms = [match("a", "eng.1", 15, self.DAY, service="peacock"), match("b", "eng.1", 10, self.DAY),
              match("c", "eng.1", 12, self.DAY, state="post", status="FT", service="peacock"),
              match("d", "esp.1", 14, date(2026, 10, 8))]
        week = build.schedule_by_day(ms, self.DAY, days=3)
        self.assertEqual([d["date"] for d in week], ["2026-10-07", "2026-10-08", "2026-10-09"])
        self.assertEqual(week[0]["weekday"], "Wednesday")
        pl = week[0]["competitions"][build.LEAGUES["eng.1"]["name"]]
        self.assertEqual(pl, {"matches": 3, "on_owner_services": 2, "finished": 1, "first_kickoff": "10:00 AM ET"})
        self.assertEqual(list(week[1]["competitions"]), [build.LEAGUES["esp.1"]["name"]])
        self.assertEqual(week[2]["competitions"], {})

    def test_called_off_and_hidden_competitions_left_out(self):
        hidden = next(lg for lg, info in build.LEAGUES.items() if info.get("default_off"))
        ms = [match("a", "eng.1", 15, self.DAY, state="post", status="Postponed"), match("b", hidden, 19, self.DAY)]
        self.assertEqual(build.schedule_by_day(ms, self.DAY, days=1)[0]["competitions"], {})

    def test_time_to_be_set(self):
        ms = [match("a", "eng.1", 0, self.DAY, time_valid=False), match("b", "eng.1", 20, self.DAY)]
        pl = build.schedule_by_day(ms, self.DAY, days=1)[0]["competitions"][build.LEAGUES["eng.1"]["name"]]
        self.assertEqual(pl["first_kickoff"], "8:00 PM ET")
        self.assertEqual(pl["matches"], 2)

    def test_eastern_dates(self):
        # 9 pm Eastern on the 7th is 01:00 UTC on the 8th, and belongs to the 7th, as ESPN files it.
        ms = [match("a", "eng.1", 21, self.DAY)]
        self.assertEqual(ms[0].utc.date(), date(2026, 10, 8))
        week = build.schedule_by_day(ms, self.DAY, days=2)
        self.assertEqual(len(week[0]["competitions"]), 1)
        self.assertEqual(week[1]["competitions"], {})


if __name__ == "__main__":
    unittest.main()
