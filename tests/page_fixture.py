"""Small, synthetic schedule shared by rendering and offline browser checks."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timezone
import os
import tempfile
from unittest.mock import patch


BUILT_AT = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)
TODAY = date(2026, 10, 7)


# settings.toml's first values, fixed here: the tests render with these, so that tuning the real file
# or switching AI off never fails a test, and so never holds up a publish. A test of the switch says
# which way it wants it.
FIXTURE_SETTINGS = """
[ai]
enabled = true

[blend]
claude = 50
outlook = 50
interest = 80
league_priority = 20

[outlook]
weights = { stature = 40, close = 25, stakes = 15, tv = 10, goals = 10 }
missing = 0.5
stature_full = 150
draw_from = 0.10
draw_to = 0.30
bottom = 0.6
goals_from = 2.0
goals_to = 4.0
knockout = { "final" = 1.0, "semifinal" = 0.9, "quarterfinal" = 0.8, "round of 16" = 0.7, "playoff" = 0.7 }
network = ["ABC", "CBS", "FOX", "NBC", "Telemundo", "Univision", "UniMás"]
cable = ["ESPN", "ESPN2", "FS1", "FS2", "USA Network"]
"""


def fixture_settings(builder, **changes):
    """FIXTURE_SETTINGS read through the real loader, with `changes` (such as ai=False) applied."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "settings.toml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(FIXTURE_SETTINGS)
        settings = builder.load_settings(path, {o["label"] for o in builder.OUTLETS.values()})
    return replace(settings, **changes)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return BUILT_AT.astimezone(tz) if tz else BUILT_AT.replace(tzinfo=None)


def render_page(builder, *, fragment=False, fixtures=None, failed=(), facts_path=None, team_names=None, league_logos=False,
                tbd=(), ai=True, stature=None, odds=None):
    """Exercise the real renderer with fixed time, rights, teams, scores, a table and settings.

    Fixtures named in `tbd` have a kickoff time to be set (ESPN's timeValid false); their kickoff
    is then only a placeholder on the right day. `ai` is the settings' switch; `stature` maps a
    fixture to its stature score (150 otherwise), `odds` to its (draw chance, goal line). A baseline
    build from before the settings existed renders without them.
    """
    scored = hasattr(builder, "load_settings")
    with (patch.object(builder, "SETTINGS", fixture_settings(builder, ai=ai)) if scored else nullcontext(),
          patch.object(builder, "TODAY", TODAY),
          patch.object(builder, "datetime", FixedDatetime),
          patch.dict(builder.UNKNOWN_OUTLETS, {}, clear=True),
          patch.dict(builder.LEAGUE_LOGOS, {}, clear=True),
          patch.dict(builder.STANDINGS, {}, clear=True)):
        if fixtures is None:
            fixtures = [
                ("finished", "2026-10-07T14:00:00+00:00", "post", "ESPN+"),
                ("live", "2026-10-07T16:30:00+00:00", "in", "ESPN+"),
                ("upcoming", "2026-10-07T17:05:00+00:00", "pre", "ESPN+"),
                ("unknown", "2026-10-07T23:00:00+00:00", "pre", "Mystery Sports+"),
                ("midnight", "2026-10-08T04:30:00+00:00", "pre", "ESPN+"),
                ("late", "2026-10-08T07:30:00+00:00", "pre", "ESPN+"),
                ("dawn", "2026-10-08T08:00:00+00:00", "pre", "ESPN+"),
                ("usual", "2026-10-08T18:00:00+00:00", "pre", None),
                ("later", "2026-10-10T18:00:00+00:00", "pre", "ESPN+"),
            ]
        matches = []
        for fixture in fixtures:
            match_id, kickoff, state, channel = fixture[:4]
            league = fixture[4] if len(fixture) > 4 else "eng.1"
            home_name, away_name = (team_names or {}).get(match_id, ("Arsenal", "Chelsea"))
            home = builder.Team(home_name, "ARS", "", "", id="1", color="ef0107",
                                score="2" if state == "post" else "0", form="WWDLW",
                                rank=1, pts="18", size=2, leader="A. Player", leader_goals="6")
            away = builder.Team(away_name, "CHE", "", "", id="2", color="034694",
                                score="1" if state == "post" else "0", form="WLWDW",
                                rank=2, pts="15", size=2)
            outlets = [builder.map_outlet(channel, league)] if channel else []
            rule = builder.usual_home(league, home.name) if not outlets else None
            service, basis, outlet = builder.evaluate(outlets, rule, set(builder.OWNER))
            matches.append(builder.Match(
                id=match_id, utc=datetime.fromisoformat(kickoff), time_valid=match_id not in tbd,
                league=league, comp=builder.LEAGUES[league]["name"], stage="", note="",
                home=home, away=away, venue="Fixture Stadium", state=state,
                status="FT" if state == "post" else "30'" if state == "in" else "",
                outlets=outlets, rule=rule, hint="", service=service, basis=basis,
                outlet=outlet, score=(stature or {}).get(match_id, 150),
                **(dict(zip(("draw", "goal_line"), (odds or {}).get(match_id, (None, None)))) if scored else {})))
        table = [dict(id=t.id, name=t.name, rank=t.rank, pts=t.pts, logo="",
                      gp="8", rec="6-0-2", gd="+10") for t in (home, away)]
        builder.STANDINGS["eng.1"] = {"tables": [("", table)]}
        if facts_path is not None:
            builder.write_facts(facts_path, matches, BUILT_AT, TODAY)
        cache = {}
        if league_logos:
            for i, league in enumerate(dict.fromkeys(m.league for m in matches)):
                url = f'https://fixture.example/leaguelogos/soccer/500/{i}.png'
                builder.LEAGUE_LOGOS[league] = url
                cache[builder.logo_key(url)] = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='32' height='32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23905bc9'/%3E%3C/svg%3E"
        page = builder.build_page(matches, cache, BUILT_AT, failed, TODAY)
        return page if fragment else builder.as_document(page)


def scoreboard(state):
    """A minimal ESPN-shaped update for the upcoming fixture."""
    return {"events": [{
        "id": "upcoming",
        "competitions": [{
            "status": {"type": {"state": state, "description": "Final" if state == "post" else "In Progress"},
                       "displayClock": "63'"},
            "competitors": [
                {"homeAway": "home", "score": "2", "team": {"id": "1", "abbreviation": "ARS"}},
                {"homeAway": "away", "score": "1", "team": {"id": "2", "abbreviation": "CHE"}},
            ],
            "details": [{"scoringPlay": True, "team": {"id": "1"},
                         "clock": {"displayValue": "63'"},
                         "athletesInvolved": [{"shortName": "A. Player"}]}],
        }],
    }]}
