"""Small, synthetic schedule shared by rendering and offline browser checks."""
from datetime import date, datetime, timezone
from unittest.mock import patch


BUILT_AT = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)
TODAY = date(2026, 10, 7)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return BUILT_AT.astimezone(tz) if tz else BUILT_AT.replace(tzinfo=None)


def render_page(builder, *, fragment=False):
    """Exercise the real renderer with fixed time, rights, teams, scores and a table."""
    with (patch.object(builder, "TODAY", TODAY),
          patch.object(builder, "datetime", FixedDatetime),
          patch.dict(builder.UNKNOWN_OUTLETS, {}, clear=True),
          patch.dict(builder.LEAGUE_LOGOS, {}, clear=True),
          patch.dict(builder.STANDINGS, {}, clear=True)):
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
        for match_id, kickoff, state, channel in fixtures:
            home = builder.Team("Arsenal", "ARS", "", "", id="1", color="ef0107",
                                score="2" if state == "post" else "0", form="WWDLW",
                                rank=1, pts="18", size=2, leader="A. Player", leader_goals="6")
            away = builder.Team("Chelsea", "CHE", "", "", id="2", color="034694",
                                score="1" if state == "post" else "0", form="WLWDW",
                                rank=2, pts="15", size=2)
            outlets = [builder.map_outlet(channel, "eng.1")] if channel else []
            rule = builder.usual_home("eng.1", home.name) if not outlets else None
            service, basis, outlet = builder.evaluate(outlets, rule, set(builder.OWNER))
            matches.append(builder.Match(
                id=match_id, utc=datetime.fromisoformat(kickoff), time_valid=True,
                league="eng.1", comp="Premier League", stage="", note="",
                home=home, away=away, venue="Fixture Stadium", state=state,
                status="FT" if state == "post" else "30'" if state == "in" else "",
                outlets=outlets, rule=rule, hint="", service=service, basis=basis,
                outlet=outlet, score=150))
        table = [dict(id=t.id, name=t.name, rank=t.rank, pts=t.pts, logo="",
                      gp="8", rec="6-0-2", gd="+10") for t in (home, away)]
        builder.STANDINGS["eng.1"] = {"tables": [("", table)]}
        page = builder.build_page(matches, {}, BUILT_AT, [], TODAY)
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
