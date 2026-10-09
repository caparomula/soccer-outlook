#!/usr/bin/env python3
"""Build Soccer Outlook, a soccer schedule filtered by the viewer's US services and leagues.

Fetch fixtures and standings from ESPN, map US broadcasters through rights.toml, and write the main
HTML file with web/page.html, web/styles.css, web/live.js and web/app.js embedded. Full builds also write
three public schedule pages, a sitemap, a PNG favicon and an IndexNow ownership file. The default
fetch covers six Eastern calendar dates: yesterday, today and four days ahead. The browser displays today and
three later days, with each day starting at 4 am in the viewer's time zone, plus recent results.

rights.toml supplies listed-channel mappings and established usual coverage when ESPN has not
posted a broadcaster. Usual coverage is labelled "usually". OWNER and LEAGUES define defaults;
browser preferences decide which matches a viewer sees. settings.toml configures AI and scoring.
This script calculates the Outlook score but makes no AI calls; story.py writes optional story.json.

The browser checks live scores while relevant matches are on. GitHub Actions rebuilds the fixtures
and broadcaster listings three times a day. The page warns when its build is over 30 hours old.

Images normally load from ESPN. --embed-images includes them as data URIs, using --logos as a cache;
it does not make live scores, fonts or story loading work offline. --facts writes input for story.py,
--warnings writes coverage issues for maintainers, and --report records source completeness and
publication status. See docs/development.md for the full workflow.

Usage: python3 build.py --out site/index.html [--days-ahead 4] [--days-back 1] [--warnings FILE] [--report FILE]
                        [--date YYYY-MM-DD] [--embed-images --logos logos.json] [--fragment] [--workers 6]

The build fails if no usable scoreboards arrive, more than half fail, or an empty schedule has
missing sources or unreadable events. A healthy empty slate is valid. Partial failures appear in
the page footer, build log and JSON report.
"""
import argparse
import base64
import collections
import concurrent.futures as cf
import gzip
import html
import json
import math
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import providers
import story_state  # shared window and candidate limits; the builder does not import AI generation
import discovery

ET = ZoneInfo("America/New_York")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={day}&limit=200"
LOGO_PX = 48            # team and league logos are shown at 32 CSS px; 48 covers high-density screens
HEAD_PX = 48
CACHE_FORMAT = 2        # bump when the image size or key scheme changes; other formats are discarded
# Observed on 2026-10-06: the artifact host refused a 3.76 MB page to signed-out viewers while it
# served 3.63 MB and 2.96 MB pages. The exact limit is not documented, so stay well below both.
PAGE_BUDGET = 3_300_000  # bytes
LIVE_MINUTES = 125

# ----------------------------------------------------------------------------------------------
# Leagues to track, by ESPN's league id. tier: 1 marquee, 2 solid, 3 background. default_off:
# hidden until the viewer turns the competition pill on. short: at most four letters, standing in
# for the league's emblem in the bar's league strip when ESPN has no emblem for it (or the page is
# built without images). Where each one is shown in the US (its usual home, or a hint) is in
# rights.toml.
#
# Insertion order sets the default league priority used by the strip, filter panel and pick score.
# It is an editorial preference for a US audience, informed by audience reporting reviewed on
# 8 October 2026. Those figures are not directly comparable across competitions; docs/scoring.md
# preserves the sources, limitations and full order. Visitors can save their own order.
# ----------------------------------------------------------------------------------------------
LEAGUES = {
    "uefa.champions": dict(name="Champions League", short="UCL", tier=1),
    "eng.1": dict(name="Premier League", short="EPL", tier=1),
    "mex.1": dict(name="Liga MX", short="LMX", tier=2),
    "usa.1": dict(name="MLS", short="MLS", tier=2),
    "esp.1": dict(name="La Liga", short="LIGA", tier=1),
    "fifa.friendly": dict(name="Men's friendly", short="FRI", tier=2),
    "usa.nwsl": dict(name="NWSL", short="NWSL", tier=2, default_off=True),
    "concacaf.nations.league": dict(name="Concacaf Nations League", short="CNL", tier=3),
    "ita.1": dict(name="Serie A", short="SERA", tier=1),
    "ger.1": dict(name="Bundesliga", short="BUND", tier=1),   # Versant from 2026-27: 30+ on USA Network, the rest free on Fandango
    "uefa.europa": dict(name="Europa League", short="UEL", tier=2, default_off=True),
    "concacaf.champions": dict(name="Concacaf Champions Cup", short="CCC", tier=2),
    "fifa.friendly.w": dict(name="Women's friendly", short="WFRI", tier=2, default_off=True),
    "eng.fa": dict(name="FA Cup", short="FAC", tier=2),
    "uefa.nations": dict(name="Nations League", short="UNL", tier=2),
    "fra.1": dict(name="Ligue 1", short="L1", tier=2, default_off=True),
    "esp.copa_del_rey": dict(name="Copa del Rey", short="CDR", tier=2),
    "eng.league_cup": dict(name="Carabao Cup", short="EFLC", tier=2),
    "usa.usl.1": dict(name="USL Championship", short="USLC", tier=3, default_off=True),
    "eng.2": dict(name="Championship", short="EFL", tier=3),
    "uefa.europa.conf": dict(name="Conference League", short="UECL", tier=3, default_off=True),
    "usa.open": dict(name="U.S. Open Cup", short="USOC", tier=3),
    "uefa.wchampions": dict(name="Women's Champions League", short="UWCL", tier=2),
    "eng.w.1": dict(name="Women's Super League", short="WSL", tier=2),
    "ita.coppa_italia": dict(name="Coppa Italia", short="COPI", tier=2),
    "ger.dfb_pokal": dict(name="DFB-Pokal", short="DFB", tier=2),
    "sco.1": dict(name="Scottish Premiership", short="SPFL", tier=3),
    "ned.1": dict(name="Eredivisie", short="ERE", tier=2, default_off=True),
    "ksa.1": dict(name="Saudi Pro League", short="SPL", tier=3),
    "caf.nations": dict(name="Africa Cup of Nations", short="AFCN", tier=2),
    "por.1": dict(name="Primeira Liga", short="POR", tier=3),
    "arg.1": dict(name="Liga Profesional (Argentina)", short="ARG", tier=3),
    "bra.1": dict(name="Brasileirão", short="BRA", tier=3),
    "usa.usl.l1": dict(name="USL League One", short="USL1", tier=3, default_off=True),
}

# ----------------------------------------------------------------------------------------------
# Who shows what. The facts (which services carry which channels, the names ESPN uses for each
# channel, and where each competition usually lives) are in rights.toml, each with its source and
# the date it was checked, because they change with every season's rights deals and carriage
# disputes. load_rights() reads and checks that file and the tables below are derived from it, so
# a fact lives in one place. OWNER is the page owner's lineup, the default for every viewer until
# they pick their own in the page; the choice stays in that viewer's browser.
# ----------------------------------------------------------------------------------------------
RIGHTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rights.toml")
OWNER = ["hbo", "fox", "para", "espn", "apple", "usa", "prime", "netflix", "disney"]
# Teams whose matches are shown by default even in a competition that is off by default (LEAGUES'
# default_off), identified by ESPN's stable team IDs: switching women's friendlies off hid the USWNT
# against the world champions. A viewer who switches the competition off still hides them.
FEATURED_TEAMS = {"660", "2765"}   # United States men and women
STALE_AFTER_DAYS = 180      # an entry in rights.toml not checked for this long is reported
LAPSE_NOTICE_DAYS = 30      # a usual home is reported this long before its season ends
TODAY = datetime.now(ET).date()   # the build's Eastern date; main() sets it, --date included


class RightsError(ValueError):
    """rights.toml contradicts itself or this script; the message lists every problem found."""


@dataclass(frozen=True)
class UsualHome:
    channel: str         # carries every match not listed yet; "" when the rights go club by club only
    by_home: dict        # ESPN home team ID -> channel, for competitions sold club by club
    season: str
    until: date          # the claims lapse after this day


@dataclass(frozen=True)
class LeagueRights:
    usual: object        # a UsualHome, or None
    hint: str


@dataclass(frozen=True)
class Rights:
    services: dict       # service id -> display name, in order of preference
    outlets: dict        # every name ESPN may use for a channel, lower case -> _o(label, via, free, es)
    simulcasts: tuple    # (channel, service id, frozenset of league ids)
    leagues: dict        # league id -> LeagueRights
    checked: tuple       # (what, date last checked) for every entry that carries a date


def _o(label, via, free=False, es=False):
    return dict(label=label, via=via, free=free, es=es)


def load_rights(path, leagues):
    """Reads rights.toml and checks it against itself and against `leagues`, the tracked ids.

    Every problem is collected before raising, so one run lists them all. The checks are the ways
    an edit can quietly break the page: a service naming a channel that isn't defined, two channels
    claiming the same ESPN name, a usual home no service carries or with no season end, a misspelt
    key that would otherwise be ignored, and a fact with no source or check date."""
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise RightsError(f"{os.path.basename(path)}: {e}") from None
    problems = []
    real_today = datetime.now(ET).date()

    def keys(where, entry, allowed):
        extra = sorted(set(entry) - allowed)
        if extra:
            problems.append(f"{where}: unknown key {', '.join(extra)} (expected {', '.join(sorted(allowed))})")

    def text(where, entry, key, required=True):
        v = entry.get(key)
        if v is None:
            if required:
                problems.append(f"{where}: missing {key}")
            return ""
        if not isinstance(v, str) or not v.strip():
            problems.append(f"{where}: {key} must be text")
            return ""
        return v.strip()

    def day(where, entry, key):
        v = entry.get(key)
        if not isinstance(v, date) or isinstance(v, datetime):
            problems.append(f"{where}: {key} must be a date such as 2026-10-06")
            return None
        if key == "checked" and v > real_today + timedelta(days=1):
            problems.append(f"{where}: checked {v} is in the future")
        return v

    def table(where, v):
        if not isinstance(v, dict):
            problems.append(f"{where}: must be a table")
            return False
        return True

    keys("rights.toml", data, {"channels", "services", "simulcasts", "leagues"})

    channels = data.get("channels") or {}
    names = {}                                  # lower-case name -> channel
    for label, ch in channels.items():
        where = f"channels.{label!r}"
        if not table(where, ch):
            continue
        keys(where, ch, {"espn", "free", "es"})
        for flag in ("free", "es"):
            if flag in ch and not isinstance(ch[flag], bool):
                problems.append(f"{where}: {flag} must be true or false")
        aliases = ch.get("espn", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            problems.append(f"{where}: espn must be a list of names")
            aliases = []
        for n in [label, *aliases]:
            key = n.strip().lower()
            if names.get(key, label) != label:
                problems.append(f"{where}: the name {n!r} also belongs to {names[key]!r}")
            names[key] = label

    services = data.get("services") or {}
    if not services:
        problems.append("no [services]")
    carried = {label: [] for label in channels}  # channel -> service ids, in order of preference
    checked = []
    for sid, svc in services.items():
        where = f"services.{sid}"
        if not re.fullmatch(r"[a-z][a-z0-9]*", sid):
            problems.append(f"{where}: an id is lower-case letters and digits")
        if not table(where, svc):
            continue
        keys(where, svc, {"name", "channels", "note", "source", "checked"})
        text(where, svc, "name")
        text(where, svc, "note", required=False)
        text(where, svc, "source")
        when = day(where, svc, "checked")
        if when:
            checked.append((f"{svc.get('name', sid)} (services.{sid})", when))
        chs = svc.get("channels")
        if not isinstance(chs, list) or not chs:
            problems.append(f"{where}: channels must be a list of channel names")
            continue
        if len(set(chs)) != len(chs):
            problems.append(f"{where}: a channel is listed twice")
        for c in chs:
            if c not in channels:
                problems.append(f"{where}: no channel {c!r} under [channels]")
            elif sid not in carried[c]:
                carried[c].append(sid)

    simulcasts = []
    for i, sc in enumerate(data.get("simulcasts") or []):
        where = f"simulcasts[{i}]"
        if not table(where, sc):
            continue
        keys(where, sc, {"channel", "service", "leagues", "note", "source", "checked"})
        c, sid = text(where, sc, "channel"), text(where, sc, "service")
        text(where, sc, "note", required=False)
        text(where, sc, "source")
        when = day(where, sc, "checked")
        if when:
            checked.append((f"{c} on {sid} (simulcasts)", when))
        lgs = sc.get("leagues")
        if c and c not in channels:
            problems.append(f"{where}: no channel {c!r} under [channels]")
        if sid and sid not in services:
            problems.append(f"{where}: no service {sid!r}")
        if not isinstance(lgs, list) or not lgs:
            problems.append(f"{where}: leagues must be a list of league ids")
            lgs = []
        for lg in lgs:
            if lg not in leagues:
                problems.append(f"{where}: {lg!r} is not a competition build.py tracks")
        simulcasts.append((c, sid, frozenset(lgs)))

    league_rights = {}
    for lg, e in (data.get("leagues") or {}).items():
        where = f"leagues.{lg!r}"
        if lg not in leagues:
            problems.append(f"{where}: not a competition build.py tracks")
        if not table(where, e):
            continue
        keys(where, e, {"usual", "by_home_team", "season", "until", "hint", "note", "source", "checked"})
        hint = text(where, e, "hint", required=False)
        text(where, e, "note", required=False)
        text(where, e, "source")
        when = day(where, e, "checked")
        if when:
            checked.append((f"{leagues.get(lg, {}).get('name', lg)} (leagues.{lg})", when))
        usual = None
        if {"usual", "by_home_team", "season", "until"} & set(e):
            by_home = e.get("by_home_team", {})
            if not isinstance(by_home, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in by_home.items()):
                problems.append(f"{where}: by_home_team must map ESPN team IDs to channel names")
                by_home = {}
            for team_id in by_home:
                if not re.fullmatch(r"[1-9][0-9]*", team_id):
                    problems.append(f"{where}: by_home_team key {team_id!r} must be a numeric ESPN team ID")
            ch = text(where, e, "usual", required=not by_home)
            season, until = text(where, e, "season"), day(where, e, "until")
            for label, what in [(ch, "usual")] + [(c, f"by_home_team.{club!r}") for club, c in by_home.items()]:
                if label and label not in channels:
                    problems.append(f"{where}: {what} names no channel {label!r} under [channels]")
                elif label and not carried[label]:
                    problems.append(f"{where}: {what}: no service carries {label!r}, so it can't be a usual home")
            if until and when and until < when:
                problems.append(f"{where}: until {until} is before checked {when}")
            if (ch or by_home) and season and until:
                usual = UsualHome(ch, dict(by_home), season, until)
        elif not hint:
            problems.append(f"{where}: give a usual home (usual or by_home_team, with season and until) or a hint")
        league_rights[lg] = LeagueRights(usual, hint)

    if problems:
        raise RightsError(f"{os.path.basename(path)} has {len(problems)} problem(s):\n  " + "\n  ".join(problems))
    outlets = {}
    for key, label in names.items():
        ch = channels[label]
        outlets[key] = _o(label, list(carried[label]), free=bool(ch.get("free")), es=bool(ch.get("es")))
    return Rights(services={sid: svc["name"] for sid, svc in services.items()}, outlets=outlets,
                  simulcasts=tuple(simulcasts), leagues=league_rights, checked=tuple(checked))


RIGHTS = load_rights(RIGHTS_PATH, LEAGUES)
SERVICES = RIGHTS.services
SERVICE_RANK = list(SERVICES)
OUTLETS = RIGHTS.outlets
if set(OWNER) - set(SERVICES):
    raise RightsError(f"OWNER names services rights.toml doesn't define: {sorted(set(OWNER) - set(SERVICES))}")


# ----------------------------------------------------------------------------------------------
# settings.toml holds what the owner tunes: whether AI takes part and which model does what, how a pick
# score blends its parts, and the recipe for the page's own Outlook score. load_settings() checks it as
# load_rights() checks rights.toml, so a typo stops the build instead of quietly changing every pick.
# The [ai] table is checked by providers.check_ai, the same check story.py makes before it spends.
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.toml")
OUTLOOK_PARTS = ("stature", "close", "stakes", "tv", "goals")


class SettingsError(ValueError):
    """settings.toml is malformed; the message lists every problem found."""


@dataclass(frozen=True)
class Settings:
    ai: bool             # whether an AI model takes part at all
    design: str          # "ratings" (scores only) or "full" (Claude's research, overview and blurbs too)
    model: str           # the model that does it, from providers.MODELS
    effort: str
    overview_model: str | None     # the ratings design's overview, written after a web search; None for none
    overview_effort: str | None
    blurbs_model: str | None       # the ratings design's blurbs for the top picks, likewise
    blurbs_effort: str | None
    blend: dict          # ai, outlook, interest, league_priority: weights, at least one of each pair above 0
    outlook: dict        # the [outlook] table as checked; knockout words in lower case, channel lists as sets
    dots: tuple          # the four pick scores where a match's second to fifth golden dots begin

    @property
    def provider(self):
        return providers.MODELS[self.model]


def load_settings(path, channels):
    """Reads settings.toml and checks every value against `channels`, the channel names rights.toml
    defines. Every problem is collected before raising, so one run lists them all."""
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise SettingsError(f"{os.path.basename(path)}: {e}") from None
    problems = []

    def table(where, value, expected):
        if not isinstance(value, dict):
            problems.append(f"{where}: must be a table")
            return {}
        extra = sorted(set(value) - set(expected))
        if extra:
            problems.append(f"{where}: unknown key {', '.join(extra)} (expected {', '.join(expected)})")
        absent = [k for k in expected if k not in value]
        if absent:
            problems.append(f"{where}: missing {', '.join(absent)}")
        return value

    def number(where, value, low, high=None):
        """A finite number within [low, high], or None (an absent value is reported as missing)."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            problems.append(f"{where}: must be a number")
            return None
        if value < low or (high is not None and value > high):
            problems.append(f"{where}: must be from {low} to {high}" if high is not None else f"{where}: must be at least {low}")
            return None
        return float(value)

    def rising(where, low, high):
        if low is not None and high is not None and low >= high:
            problems.append(f"{where}: the first must be below the second")

    def names(where, value):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            problems.append(f"{where}: must be a list of channel names")
            return frozenset()
        unknown = [v for v in value if v not in channels]
        if unknown:
            problems.append(f"{where}: {', '.join(unknown)} not a channel in rights.toml")
        return frozenset(value)

    top = table(os.path.basename(path), data, ("ai", "blend", "outlook", "dots"))
    ai, ai_problems = providers.check_ai(top.get("ai", {}))
    problems += ai_problems

    b = table("[blend]", top.get("blend", {}), ("ai", "outlook", "interest", "league_priority"))
    blend = {k: number(f"[blend] {k}", b.get(k), 0) for k in ("ai", "outlook", "interest", "league_priority")}
    for pair in (("ai", "outlook"), ("interest", "league_priority")):
        if all(blend[k] is not None for k in pair) and sum(blend[k] for k in pair) <= 0:
            problems.append(f"[blend] {' and '.join(pair)}: at least one must be above 0")

    o = table("[outlook]", top.get("outlook", {}), ("weights", "missing", "stature_full", "draw_from", "draw_to", "bottom",
                                                    "goals_from", "goals_to", "knockout", "network", "cable"))
    w = table("[outlook] weights", o.get("weights", {}), OUTLOOK_PARTS)
    weights = {k: number(f"[outlook] weights.{k}", w.get(k), 0) for k in OUTLOOK_PARTS}
    if all(v is not None for v in weights.values()) and sum(weights.values()) <= 0:
        problems.append("[outlook] weights: at least one must be above 0")
    outlook = dict(weights=weights,
                   missing=number("[outlook] missing", o.get("missing"), 0, 1),
                   stature_full=number("[outlook] stature_full", o.get("stature_full"), 1),
                   draw_from=number("[outlook] draw_from", o.get("draw_from"), 0, 1),
                   draw_to=number("[outlook] draw_to", o.get("draw_to"), 0, 1),
                   bottom=number("[outlook] bottom", o.get("bottom"), 0, 1),
                   goals_from=number("[outlook] goals_from", o.get("goals_from"), 0, 20),
                   goals_to=number("[outlook] goals_to", o.get("goals_to"), 0, 20))
    rising("[outlook] draw_from, draw_to", outlook["draw_from"], outlook["draw_to"])
    rising("[outlook] goals_from, goals_to", outlook["goals_from"], outlook["goals_to"])
    knockout = o.get("knockout", {})
    if not isinstance(knockout, dict):
        problems.append("[outlook] knockout: must be a table of stage words")
        knockout = {}
    outlook["knockout"] = {}
    for word, value in knockout.items():
        v = number(f"[outlook] knockout.{word}", value, 0, 1)
        if not word.strip():
            problems.append("[outlook] knockout: a stage word can't be empty")
        elif v is not None:
            outlook["knockout"][word.strip().lower()] = v
    outlook["network"] = names("[outlook] network", o.get("network", []))
    outlook["cable"] = names("[outlook] cable", o.get("cable", []))
    both = sorted(outlook["network"] & outlook["cable"])
    if both:
        problems.append(f"[outlook] network and cable both list {', '.join(both)}")
    d = table("[dots]", top.get("dots", {}), ("thresholds",))
    dots = d.get("thresholds")
    if not (isinstance(dots, list) and len(dots) == 4
            and all(isinstance(t, (int, float)) and not isinstance(t, bool) and 0 < t <= 100 for t in dots)
            and all(a < b for a, b in zip(dots, dots[1:]))):
        problems.append("[dots] thresholds: must be four rising scores from above 0 to 100, where a match's second to fifth dots begin")
        dots = None
    if problems:
        raise SettingsError(f"{os.path.basename(path)}:\n  " + "\n  ".join(problems))
    return Settings(ai=ai["enabled"], design=ai["design"], model=ai["model"], effort=ai["effort"],
                    overview_model=ai["overview_model"], overview_effort=ai["overview_effort"],
                    blurbs_model=ai["blurbs_model"], blurbs_effort=ai["blurbs_effort"], blend=blend, outlook=outlook,
                    dots=tuple(float(t) for t in dots))


SETTINGS = load_settings(SETTINGS_PATH, {o["label"] for o in OUTLETS.values()})


# ESPN team IDs, checked against its team catalogs on 2026-10-09. Values are labels for maintainers;
# scoring looks up IDs only, so alternate spellings and renamed teams keep the same treatment.
# Men and women have distinct IDs even when their display names are identical.
MARQUEE_CLUBS = {
    "103": "AC Milan",
    "104": "AS Roma",
    "139": "Ajax Amsterdam",
    "929": "Al Hilal",
    "2276": "Al Ittihad",
    "817": "Al Nassr",
    "227": "América",
    "359": "Arsenal",
    "19973": "Arsenal",
    "1068": "Atlético Madrid",
    "83": "Barcelona",
    "20091": "Barcelona",
    "131": "Bayer Leverkusen",
    "132": "Bayern Munich",
    "20103": "Bayern Munich",
    "1929": "Benfica",
    "20830": "Benfica",
    "5": "Boca Juniors",
    "124": "Borussia Dortmund",
    "256": "Celtic",
    "363": "Chelsea",
    "19970": "Chelsea",
    "218": "Cruz Azul",
    "437": "FC Porto",
    "142": "Feyenoord Rotterdam",
    "819": "Flamengo",
    "15364": "Gotham FC",
    "219": "Guadalajara",
    "20232": "Inter Miami CF",
    "110": "Internazionale",
    "131635": "Internazionale",
    "111": "Juventus",
    "20092": "Juventus",
    "20907": "Kansas City Current",
    "187": "LA Galaxy",
    "18966": "LAFC",
    "364": "Liverpool",
    "19971": "Liverpool",
    "382": "Manchester City",
    "19257": "Manchester City",
    "360": "Manchester United",
    "20061": "Manchester United",
    "176": "Marseille",
    "114": "Napoli",
    "361": "Newcastle United",
    "18206": "Orlando Pride",
    "148": "PSV Eindhoven",
    "2029": "Palmeiras",
    "160": "Paris Saint-Germain",
    "19258": "Paris Saint-Germain",
    "257": "Rangers",
    "86": "Real Madrid",
    "21128": "Real Madrid",
    "16": "River Plate",
    "21685": "Roma",
    "2250": "Sporting CP",
    "367": "Tottenham Hotspur",
    "20062": "Tottenham Hotspur",
    "15365": "Washington Spirit",
}
MARQUEE_NATIONS = {
    "202": "Argentina",
    "2750": "Argentina",
    "628": "Australia",
    "2751": "Australia",
    "459": "Belgium",
    "18736": "Belgium",
    "205": "Brazil",
    "2752": "Brazil",
    "206": "Canada",
    "2753": "Canada",
    "208": "Colombia",
    "11337": "Colombia",
    "477": "Croatia",
    "479": "Denmark",
    "2896": "Denmark",
    "448": "England",
    "5159": "England",
    "478": "France",
    "2755": "France",
    "481": "Germany",
    "2756": "Germany",
    "162": "Italy",
    "2792": "Italy",
    "627": "Japan",
    "2758": "Japan",
    "203": "Mexico",
    "2812": "Mexico",
    "2869": "Morocco",
    "18221": "Morocco",
    "449": "Netherlands",
    "7151": "Netherlands",
    "464": "Norway",
    "2762": "Norway",
    "482": "Portugal",
    "9531": "Portugal",
    "580": "Scotland",
    "451": "South Korea",
    "17639": "South Korea",
    "164": "Spain",
    "17640": "Spain",
    "466": "Sweden",
    "2764": "Sweden",
    "475": "Switzerland",
    "17641": "Switzerland",
    "660": "United States",
    "2765": "United States",
    "212": "Uruguay",
}
NATIONAL_LEAGUES = {"uefa.nations", "fifa.friendly", "fifa.friendly.w", "concacaf.nations.league", "caf.nations"}
TIER_BASE = {1: 100, 2: 60, 3: 30}


@dataclass
class Outlet:
    label: str
    via: list            # SERVICES keys that carry this outlet
    free: bool = False
    es: bool = False
    known: bool = True   # False for a name ESPN used that rights.toml doesn't have


@dataclass
class Team:
    name: str
    abbr: str
    logo_key: str
    logo_url: str
    score: str = ""
    winner: bool = False
    id: str = ""
    form: str = ""          # last five results as ESPN gives them, e.g. "WWDLW"
    record: str = ""        # season record W-D-L
    leader: str = ""        # top scorer's short name
    leader_goals: str = ""
    color: str = ""         # team colors as 6-digit hex, when ESPN has them
    alt: str = ""
    rank: int = 0           # table position within the league or group
    pts: str = ""
    group: str = ""         # the group or conference the rank is within, when any
    size: int = 0           # teams in that table


@dataclass
class Match:
    id: str
    utc: datetime
    time_valid: bool
    league: str
    comp: str
    stage: str
    note: str
    home: Team
    away: Team
    venue: str
    state: str          # pre | in | post
    status: str         # e.g. FT, HT, 63', Canceled
    outlets: list       # Outlets ESPN lists for the US market, English first
    rule: object        # the league's usual home as an Outlet when nothing is listed, else None
    hint: str           # where the league lives when it is on none of the services, else ""
    service: str        # the page owner's view: SERVICES key or "" when not available
    basis: str          # listed | rule | none
    outlet: str         # the outlet named in the chip
    score: int = 0
    goals: list = field(default_factory=list)   # (minute, team id, scorer, note) for played matches
    recap: str = ""
    attendance: int = 0
    link: str = ""          # ESPN's match page
    draw: object = None     # the betting market's implied chance of a draw, 0-1, when ESPN carries odds
    goal_line: object = None  # the market's over/under goal line, when ESPN carries odds


def implied_chance(moneyline):
    """The chance an American moneyline implies, margin included: +240 is 100/340, -120 is 120/220,
    "EVEN" is one half. None for anything that isn't a price (American prices are 100 or more either
    way, so anything strictly between -100 and +100 is not one)."""
    if isinstance(moneyline, str):
        text = moneyline.strip().upper()
        moneyline = "100" if text == "EVEN" else text.replace("+", "", 1)
    if isinstance(moneyline, bool):
        return None
    try:
        price = float(moneyline)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or -100 < price < 100:
        return None
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def finite_number(value):
    """A finite number from a number or numeric text ESPN sends, else None."""
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def clamp01(x):
    return max(0.0, min(1.0, x))


def outlook_parts(m, settings=None):
    """The Outlook score's parts for a match, each from 0 to 1, or None where the data says nothing:
    no odds, no table and no knockout round, no broadcaster listed yet. settings.toml says what each
    part measures and why."""
    o = (settings or SETTINGS).outlook
    parts = {"stature": clamp01(m.score / o["stature_full"])}
    parts["close"] = None if m.draw is None else clamp01((m.draw - o["draw_from"]) / (o["draw_to"] - o["draw_from"]))
    stage = (m.stage or "").lower()
    word = next((w for w in sorted(o["knockout"], key=len, reverse=True) if w in stage), None)   # "semifinal" before "final"
    if word is not None:
        parts["stakes"] = o["knockout"][word]
    elif m.home.rank and m.away.rank and m.home.size > 1 and m.away.size > 1:
        place = [1 - (min(t.rank, t.size) - 1) / (t.size - 1) for t in (m.home, m.away)]   # 1 for first, 0 for last
        parts["stakes"] = max(min(place), o["bottom"] * (1 - max(place)))
    else:
        parts["stakes"] = None
    labels = {out.label for out in m.outlets}
    # Nothing listed is normal more than a few days out, and says nothing about the match.
    parts["tv"] = None if not labels else 1.0 if labels & o["network"] else 0.5 if labels & o["cable"] else 0.0
    parts["goals"] = None if m.goal_line is None else clamp01((m.goal_line - o["goals_from"]) / (o["goals_to"] - o["goals_from"]))
    return parts


def outlook_score(parts, settings=None):
    """The Outlook score from its parts: their weighted mean, a missing part counted at `missing`,
    from 0 to 100 to one decimal."""
    o = (settings or SETTINGS).outlook
    w = o["weights"]
    weighted = sum(w[k] * (o["missing"] if parts[k] is None else parts[k]) for k in OUTLOOK_PARTS)
    return round(100 * weighted / sum(w.values()), 1)     # scaled before dividing, so exact cases stay exact


def map_outlet(name, league):
    """The Outlet for a broadcaster name ESPN lists for a match in `league`.

    A name rights.toml doesn't have is counted for the build's report and kept as an unrecognized
    outlet, which the page shows as such rather than as one the viewer doesn't have."""
    o = OUTLETS.get(name.strip().lower())
    if o is None:
        UNKNOWN_OUTLETS[name] += 1
        return Outlet(label=name, via=[], known=False)
    via = list(o["via"])
    for channel, sid, lgs in RIGHTS.simulcasts:
        if channel == o["label"] and league in lgs and sid not in via:
            via.append(sid)
    return Outlet(label=o["label"], via=via, free=o["free"], es=o["es"])


def usual_home(league, home_id=""):
    """The match's usual home as an Outlet while the season lasts; None once it has lapsed.

    For a competition sold club by club (Liga MX), the home team's ESPN ID decides. Display-name
    changes do not change its rights; an unknown ID gets no claim rather than a guess."""
    r = RIGHTS.leagues.get(league)
    u = r.usual if r else None
    if not u or TODAY > u.until:
        return None
    channel = u.by_home.get(str(home_id)) or u.channel
    if not channel:
        return None
    o = OUTLETS[channel.lower()]
    return Outlet(label=o["label"], via=list(o["via"]), free=o["free"], es=o["es"])


def league_hint(league):
    """Where the league lives, as text; says so when its usual home has lapsed unconfirmed."""
    r = RIGHTS.leagues.get(league)
    if not r:
        return ""
    if r.usual and TODAY > r.usual.until:
        was = r.usual.channel or "set club by club"
        lapsed = f"Usual US home not confirmed for this season (was {was} in {r.usual.season})"
        return lapsed + (f" · {r.hint}" if r.hint else "")
    return r.hint


def evaluate(outlets, rule, have):
    """Which of the services in `have` carries a match, and on which outlet.

    The page's own script repeats this for each viewer's lineup; this copy renders the owner's view
    into the static markup. Preference: the higher-ranked service, then the outlet that is the
    service itself (HBO Max over its TNT simulcast), then English over Spanish feeds."""
    best = None
    for o in outlets:
        for sid in o.via:
            if sid in have:
                key = (SERVICE_RANK.index(sid), o.label != SERVICES[sid], o.es)
                if best is None or key < best[0]:
                    best = (key, sid, o.label)
    if best:
        return best[1], "listed", best[2]
    if rule:
        for sid in sorted((x for x in rule.via if x in have), key=SERVICE_RANK.index):
            return sid, "rule", rule.label
    return "", "none", ""


# ----------------------------------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------------------------------
def curl_bytes(url, timeout=40, attempts=3):
    """GET through curl, which is configured for this environment's proxy; returns bytes or None."""
    for attempt in range(attempts):
        r = subprocess.run(["curl", "--compressed", "-sS", "-m", str(timeout), "-w", "\n%{http_code}", url],
                           capture_output=True)
        body, _, code = r.stdout.rpartition(b"\n")
        if r.returncode == 0 and code.strip() == b"200":
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body
        time.sleep(1.5 * (attempt + 1))
    return None


def dicts(items):
    """The entries of an ESPN list that are objects. Its lists can hold nulls (on 7 October 2026 the
    Saudi Pro League table gave Al Faisaly `logos: [null]`, and every build crashed on it), and one
    bad entry must cost at most that entry."""
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


@dataclass
class BuildQuality:
    """Source outcomes, kept separately from fixture volume: an empty healthy slate is valid."""
    sources: list = field(default_factory=list)
    skipped_events: list = field(default_factory=list)
    excluded_events: int = 0

    def skip(self, league, event_id, reason, day=None):
        entry = dict(league=league, id=str(event_id) if event_id is not None else None, reason=str(reason))
        if day is not None:
            entry["date"] = day.isoformat()
        self.skipped_events.append(entry)

    def report(self, fixtures):
        failed = [{k: v for k, v in source.items() if k != "ok"} for source in self.sources if not source["ok"]]
        requested, successful = len(self.sources), sum(source["ok"] for source in self.sources)
        reasons = []
        if not successful:
            reasons.append("No usable scoreboard responses.")
        elif len(failed) > requested / 2:
            reasons.append(f"More than half of the scoreboard requests failed ({len(failed)}/{requested}).")
        if not fixtures and (failed or self.skipped_events):
            reasons.append("An empty schedule cannot be trusted while sources or events are missing.")
        return dict(version=1, publishable=not reasons, complete=not failed and not self.skipped_events,
                    fixtures=fixtures, sources=dict(requested=requested, successful=successful, failed=failed),
                    skipped_events=list(self.skipped_events), excluded_events=self.excluded_events, reasons=reasons)


def write_build_report(path, report):
    if path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_scoreboards(days, workers, quality=None):
    """Returns ({league: {event_id: event}}, {league: logo url}, [failed (league, day)])."""
    jobs = [(lg, day) for lg in LEAGUES for day in days]
    quality = quality if quality is not None else BuildQuality()

    def one(job):
        lg, day = job
        raw = curl_bytes(ESPN.format(league=lg, day=day.strftime("%Y%m%d")))
        if raw is None:
            return lg, day, None
        try:
            return lg, day, json.loads(raw)
        except ValueError:
            return lg, day, None

    merged = {lg: {} for lg in LEAGUES}
    logos = {}
    failed = []
    with cf.ThreadPoolExecutor(workers) as ex:
        for lg, day, data in ex.map(one, jobs):
            # A 200 response with an error object is not a successful empty schedule.
            if not isinstance(data, dict) or not isinstance(data.get("events"), list):
                failed.append((lg, day))
                quality.sources.append(dict(league=lg, date=day.isoformat(), ok=False,
                                            reason="Request failed or invalid JSON." if data is None else "Scoreboard has no events list."))
                continue
            quality.sources.append(dict(league=lg, date=day.isoformat(), ok=True))
            for ev in data["events"]:
                if not isinstance(ev, dict) or not isinstance(ev.get("id"), (str, int)) or isinstance(ev.get("id"), bool) or not str(ev["id"]).strip():
                    quality.skip(lg, ev.get("id") if isinstance(ev, dict) else None, "Event has no usable ID.", day)
                    continue
                event_id = str(ev["id"])
                merged[lg][event_id] = dict(ev, id=event_id)
            league_info = dicts(data.get("leagues"))
            for lo in dicts(league_info[0].get("logos")) if league_info else []:
                if "dark" not in (lo.get("rel") or []) and lo.get("href") and lg not in logos:
                    logos[lg] = lo["href"]
    return merged, logos, failed


STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings"
NO_TABLE = {"fifa.friendly", "fifa.friendly.w", "eng.fa", "eng.league_cup", "esp.copa_del_rey", "ger.dfb_pokal",
            "ita.coppa_italia", "usa.open"}


def _stat(entry, name):
    for st in dicts(entry.get("stats")):
        if st.get("name") == name:
            return st.get("displayValue") or (str(st.get("value")) if st.get("value") is not None else "")
    return ""


def fetch_standings(leagues, workers):
    """Returns {league: {"by_team": {team id: row}, "tables": [(group name, [rows])]}}.

    A row is a dict with rank, pts, rec, gp, gd, form, name, logo and the group it belongs to. Leagues
    without a table (friendlies, cups) are skipped, as is any league whose standings do not answer."""
    def one(lg):
        raw = curl_bytes(STANDINGS_URL.format(league=lg), attempts=2)
        if raw is None:
            return lg, None
        try:
            return lg, json.loads(raw)
        except ValueError:
            return lg, None

    out = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for lg, data in ex.map(one, [lg for lg in leagues if lg not in NO_TABLE]):
            if not data:
                continue
            try:
                table = standings_of(data)
            except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError) as e:
                # A table ESPN sends in an unexpected shape costs that table, never the build.
                print(f"skip standings {lg}: {e!r}", file=sys.stderr)
                continue
            if table["by_team"]:
                out[lg] = table
    return out


def standings_of(data):
    """One league's standings answer as {"by_team": ..., "tables": ...}."""
    groups = dicts(data.get("children"))
    if not groups and isinstance(data.get("standings"), dict):
        groups = [{"name": "", "standings": data["standings"]}]
    by_team, tables = {}, []
    for g in groups:
        gname = g.get("name") or ""
        entries = dicts((g.get("standings") or {}).get("entries"))
        rows = []
        for e in entries:
            t = e.get("team") if isinstance(e.get("team"), dict) else {}
            try:
                rank = int(float(_stat(e, "rank") or 0))
            except (ValueError, OverflowError):
                rank = 0
            logo = next((lo["href"] for lo in dicts(t.get("logos")) if lo.get("href")), "")
            row = dict(id=str(t.get("id") or ""), name=t.get("displayName") or "", logo=logo,
                       rank=rank, pts=_stat(e, "points"), rec=_stat(e, "overall"), gp=_stat(e, "gamesPlayed"),
                       gd=_stat(e, "pointDifferential"), form="", group=gname, size=len(entries), note=(e.get("note") or {}).get("description") or "")
            rows.append(row)
            if row["id"]:
                by_team[row["id"]] = row
        rows.sort(key=lambda r: (r["rank"] or 999, r["name"]))
        tables.append((gname, rows))
    return {"by_team": by_team, "tables": tables}


STANDINGS = {}


def interpret_all(merged, quality=None):
    """The matches in the merged scoreboards. An event in a shape interpret() doesn't expect costs
    that event, named on stderr, never the build."""
    matches = []
    quality = quality if quality is not None else BuildQuality()
    for lg, events in merged.items():
        for ev in events.values():
            try:
                m = interpret(lg, ev)
            except (AttributeError, KeyError, ValueError, TypeError, IndexError, OverflowError) as e:
                print(f"skip {lg} {ev.get('id')}: {e!r}", file=sys.stderr)
                quality.skip(lg, ev.get("id"), str(e))
                continue
            if m:
                matches.append(m)
            else:
                quality.excluded_events += 1
    return matches


# ----------------------------------------------------------------------------------------------
# Interpretation
# ----------------------------------------------------------------------------------------------
def logo_key(url):
    """A short stable key for a logo URL: the team id or country code in the path."""
    m = re.search(r"/teamlogos/(soccer|countries)/\d+/([A-Za-z0-9_-]+)\.png", url)
    if m:
        return ("c" if m.group(1) == "soccer" else "n") + m.group(2).lower()
    m = re.search(r"/leaguelogos/soccer/\d+(?:-dark)?/(\d+)\.png", url)
    if m:
        return "L" + m.group(1)
    m = re.search(r"/guid/([0-9a-f-]+)/", url)
    if m:
        return "g" + m.group(1).replace("-", "")
    return "x" + re.sub(r"[^A-Za-z0-9]", "", url)[-24:]


def pretty_stage(league, comp):
    group = (comp.get("group") or {}).get("name") or ""
    if league == "uefa.nations":
        m = re.match(r"Group ([A-D])(\d)", group)
        if m:
            return f"League {m.group(1)} · Group {m.group(2)}"
    if group:
        return group
    return ""


def luminance(hex6):
    """Relative luminance of a 6-digit hex color (WCAG 2 definition), 0 for black to 1 for white."""
    def channel(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(hex6[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


VOID_STATUSES = ("canceled", "cancelled", "postponed")


def called_off(state, status):
    """A match ESPN has closed without playing it; its 0-0 is a placeholder, not a score."""
    return state == "post" and status.lower() in VOID_STATUSES


def featured(m):
    return m.home.id in FEATURED_TEAMS or m.away.id in FEATURED_TEAMS


def shown_by_default(m):
    """Whether the page shows the match before a viewer changes the competition filters."""
    return not LEAGUES[m.league].get("default_off") or featured(m)


def interpret(league, ev):
    competitions = dicts(ev.get("competitions"))
    if not competitions:
        raise ValueError("Event has no usable competition.")
    comp = competitions[0]
    info = LEAGUES[league]
    season_slug = (ev.get("season") or {}).get("slug") or ""
    group = (comp.get("group") or {}).get("name") or ""
    # Leave out rounds nobody is looking for: amateur Copa del Rey qualifiers and the lower
    # Concacaf Nations League tiers.
    if league == "esp.copa_del_rey" and season_slug == "qualifying-round":
        return None
    if league == "concacaf.nations.league" and not group.startswith("League A"):
        return None

    teams = {}
    table = (STANDINGS.get(league) or {}).get("by_team") or {}
    for c in dicts(comp.get("competitors")):
        t = c["team"]
        url = t.get("logo") or ""
        team = Team(name=t.get("displayName") or t.get("name") or "?", abbr=(t.get("abbreviation") or "")[:4],
                    logo_key=logo_key(url) if url else "", logo_url=url, score=str(c.get("score") or ""),
                    winner=bool(c.get("winner")), id=str(t.get("id") or ""))
        team.form = (c.get("form") or "")[-5:]
        recs = dicts(c.get("records"))
        team.record = (recs[0].get("summary") or "") if recs else ""
        for grp in dicts(c.get("leaders")):
            if grp.get("name") == "goals" and dicts(grp.get("leaders")):
                top = dicts(grp.get("leaders"))[0]
                ath = top.get("athlete") or {}
                team.leader = ath.get("shortName") or ath.get("displayName") or ""
                team.leader_goals = top.get("displayValue") or ""
                break
        for attr, key in (("color", "color"), ("alt", "alternateColor")):
            v = (t.get(key) or "").strip().lstrip("#")
            if re.fullmatch(r"[0-9A-Fa-f]{6}", v):
                setattr(team, attr, v.lower())
        # A near-white primary color vanishes on a white card; the club's second color reads better.
        if team.color and team.alt and luminance(team.color) > 0.85 and luminance(team.alt) <= 0.85:
            team.color, team.alt = team.alt, team.color
        row = table.get(team.id)
        if row:
            team.rank, team.pts, team.group, team.size = row["rank"], row["pts"], row["group"], row["size"]
            if row["rec"]:
                team.record = row["rec"]
        teams[c["homeAway"]] = team
    home, away = teams.get("home"), teams.get("away")
    if not home or not away:
        raise ValueError("Event is missing a home or away team.")

    st = comp.get("status") or ev.get("status") or {}
    stype = st.get("type") or {}
    state = stype.get("state") or "pre"
    if state not in ("pre", "in", "post"):
        raise ValueError("Event has an unrecognized match state.")
    status = ""
    if state == "in":
        status = st.get("displayClock") or stype.get("shortDetail") or "Live"
        if (stype.get("description") or "").lower().startswith("halftime"):
            status = "HT"
    elif state == "post":
        desc = (stype.get("description") or "")
        status = "FT" if desc.lower() in ("full time", "final", "full-time") else (stype.get("shortDetail") or desc or "FT")

    stage = pretty_stage(league, comp)
    if not stage and league in ("esp.copa_del_rey", "eng.fa", "eng.league_cup", "ger.dfb_pokal", "ita.coppa_italia",
                                "uefa.champions", "uefa.europa", "uefa.europa.conf", "uefa.wchampions") and season_slug:
        stage = season_slug.replace("-", " ").capitalize()
    notes = dicts(comp.get("notes"))
    note = (notes[0].get("headline") or notes[0].get("text") or "") if notes else ""

    v = comp.get("venue") or ev.get("venue") or {}
    venue = v.get("fullName") or ""
    city = (v.get("address") or {}).get("city") or ""
    if city and city.lower() not in venue.lower():
        venue = f"{venue}, {city}" if venue else city

    # Broadcasters listed by ESPN for the US market, English first.
    listed = []
    for g in dicts(comp.get("geoBroadcasts")):
        if str(g.get("region") or "us").lower() != "us":
            continue
        if (g.get("market") or {}).get("type", "National") != "National":
            continue
        name = ((g.get("media") or {}).get("shortName") or "").strip()
        if name and name not in [n for n, _ in listed]:
            listed.append((name, g.get("lang") or "en"))
    outlets = [map_outlet(name, league) for name, _ in listed]
    outlets.sort(key=lambda o: o.es)
    rule = None if outlets else usual_home(league, home.id)
    hint = league_hint(league) if not outlets and not rule else ""
    service, basis, outlet = evaluate(outlets, rule, set(OWNER))

    goals = []
    for d in dicts(comp.get("details")):
        if not d.get("scoringPlay") or d.get("shootout"):
            continue
        who = (dicts(d.get("athletesInvolved")) or [{}])[0]
        kind = "pen" if d.get("penaltyKick") else ("og" if d.get("ownGoal") else "")   # not `note`: that is ESPN's match note
        goals.append(((d.get("clock") or {}).get("displayValue") or "", str((d.get("team") or {}).get("id") or ""),
                      who.get("shortName") or who.get("displayName") or "", kind))
    heads = dicts(comp.get("headlines"))
    recap = (heads[0].get("description") or "") if heads else ""
    # The first odds entry in ESPN's scoreboard: the draw price says
    # how evenly matched the market sees the teams, the total how many goals it expects. They feed
    # the Outlook score only; the page never shows a price.
    draw = goal_line = None
    for odds in dicts(comp.get("odds")):
        price = odds.get("drawOdds")
        draw = implied_chance(price.get("moneyLine")) if isinstance(price, dict) else None
        goal_line = finite_number(odds.get("overUnder"))
        break
    try:
        attendance = int(comp.get("attendance") or 0)
    except (TypeError, ValueError):
        attendance = 0
    link = ""
    for l in dicts(ev.get("links")):
        # The page opens this link; anything but an http(s) URL from the feed is ignored.
        if isinstance(l.get("href"), str) and re.match(r"https?://", l["href"]):
            link = l["href"]
            break

    score = TIER_BASE[info["tier"]]
    if league == "uefa.nations":
        m = re.match(r"Group ([A-D])", group)
        score = {"A": 100, "B": 55, "C": 30, "D": 20}.get(m.group(1), 40) if m else 40
    team_ids = {home.id, away.id}
    if league in NATIONAL_LEAGUES:
        score += 30 * len(team_ids & MARQUEE_NATIONS.keys())
        if team_ids & FEATURED_TEAMS:
            score += 45
    else:
        score += 25 * len(team_ids & MARQUEE_CLUBS.keys())
    if called_off(state, status):
        score = 0

    kickoff = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
    if kickoff.tzinfo is None:
        raise ValueError("Kickoff is missing its time zone.")
    return Match(id=ev["id"], utc=kickoff.astimezone(timezone.utc),
                 time_valid=bool(comp.get("timeValid", True)), league=league, comp=info["name"], stage=stage,
                 note=note, home=home, away=away, venue=venue, state=state, status=status, outlets=outlets,
                 rule=rule, hint=hint, service=service, basis=basis, outlet=outlet, score=score,
                 goals=goals, recap=recap, attendance=attendance, link=link, draw=draw, goal_line=goal_line)


# ----------------------------------------------------------------------------------------------
# Logos
# ----------------------------------------------------------------------------------------------
def load_logo_cache(path):
    """The cache is {"format": CACHE_FORMAT, "logos": {key: data URI}}; any other shape or format is ignored."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("format") != CACHE_FORMAT or not isinstance(data.get("logos"), dict):
        return {}
    return {k: v for k, v in data["logos"].items() if isinstance(v, str) and v.startswith("data:image/")}


def save_logo_cache(path, cache):
    with open(path, "w") as f:
        json.dump({"format": CACHE_FORMAT, "logos": cache}, f)


def cdn_url(url, px, crop=False):
    """ESPN's resizing proxy for one of its images, at px square (twice the largest CSS size used)."""
    path = url.split("espncdn.com", 1)[-1]
    return f"https://a.espncdn.com/combiner/i?img={urllib.parse.quote(path, safe='/')}&w={px}&h={px}" + ("&scale=crop" if crop else "")


def image_links(matches, league_logos):
    """{image key: URL} for every image the page can show, for pages that link images instead of
    embedding them. A browser fetches a CSS background only for elements it renders, so rows hidden
    by filters and tables in closed panels cost nothing until shown."""
    links = {}
    for m in matches:
        for t in (m.home, m.away):
            if t.logo_key:
                links[t.logo_key] = cdn_url(t.logo_url, 96)
    for lg, url in league_logos.items():
        links[logo_key(url)] = cdn_url(url, 64)
    for st in STANDINGS.values():
        for _, rows in st["tables"]:
            for r in rows:
                if r["logo"]:
                    links.setdefault(logo_key(r["logo"]), cdn_url(r["logo"], 64))
    return links


def fetch_logos(matches, cache, workers, league_logos):
    """Downloads every team logo and league logo not yet cached."""
    wanted = {}
    for m in matches:
        for t in (m.home, m.away):
            if t.logo_key and t.logo_key not in cache:
                wanted[t.logo_key] = t.logo_url
    for lg, url in league_logos.items():
        k = logo_key(url)
        if k not in cache and any(m.league == lg for m in matches):
            wanted[k] = url

    def one(item):
        key, url = item
        path = url.split("espncdn.com", 1)[-1]
        size = f"w={HEAD_PX}&h={HEAD_PX}&scale=crop" if key.startswith("h") else f"w={LOGO_PX}&h={LOGO_PX}"
        for candidate in (f"https://a.espncdn.com/combiner/i?img={path}&{size}", url):
            raw = curl_bytes(candidate, timeout=30, attempts=2)
            if raw and raw[:4] == b"\x89PNG" and len(raw) < 200_000:
                return key, "data:image/png;base64," + base64.b64encode(raw).decode()
        return key, None

    missing = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for key, uri in ex.map(one, wanted.items()):
            if uri:
                cache[key] = uri
            else:
                missing += 1
    return len(wanted), missing


# ----------------------------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s), quote=True)


def et_parts(dt):
    t = dt.astimezone(ET)
    return t.strftime("%I:%M").lstrip("0"), t.strftime("%p").lower(), t


def logo_html(team, cache, cls="logo"):
    if team.logo_key and team.logo_key in cache:
        return f'<i class="{cls} l-{esc(team.logo_key)}" aria-hidden="true"></i>'
    return f'<i class="{cls} logo--txt" aria-hidden="true">{esc(team.abbr[:3])}</i>'


def owner_prose():
    """The owner's lineup as a phrase for the page's opening line, written from OWNER."""
    names = ["free apps" if k == "free" else SERVICES[k] for k in OWNER]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def shares(weights):
    """Whole percentages in proportion to `weights` that sum to 100 (largest remainders round up)."""
    total = sum(weights.values())
    exact = {k: 100 * v / total for k, v in weights.items()}
    out = {k: math.floor(v) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - out[k], reverse=True)[:100 - sum(out.values())]:
        out[k] += 1
    return out


OUTLOOK_WORDS = {"stature": "the occasion's stature from the competition and any marquee clubs or nations",
                 "close": "how evenly matched the betting market sees the teams",
                 "stakes": "what the table or the knockout round puts at stake",
                 "tv": "whether a broadcast network or a cable channel carries it",
                 "goals": "how many goals the market expects"}


def priority_hint():
    """The Lineup panel's account of what league priority does, with the settings' proportions."""
    b = SETTINGS.blend
    pick = shares({"interest": b["interest"], "league_priority": b["league_priority"]})
    interest = "match interest (the AI rating and the Outlook score)" if SETTINGS.ai else "the Outlook score"
    return f"A pick's score is {pick['interest']}% {interest} and {pick['league_priority']}% this order."


def about_ai():
    """The footer's account of the AI's part, or of its absence: which model, doing what."""
    if not SETTINGS.ai:
        return "No AI is used on this page: there is no overview, no match blurbs and no AI rating."
    if SETTINGS.design == "ratings":
        def who(model):
            maker = providers.PROVIDER_NAMES[providers.MODELS[model]]
            return f"{model}, {'an' if maker[0] in 'AEIOU' else 'a'} {maker} AI model"
        ratings = (f"Each morning, {who(SETTINGS.model)}, is asked to rate the matches the page shows, from "
                   "today through the third day after it, for popularity (25%), expected gameplay (35%) and competitive "
                   "impact (40%), from ESPN's table, form and stage and without seeing the Outlook score. Later rebuilds "
                   "fill missing or newly listed ratings while keeping the day's accepted ratings. Its ratings are "
                   "editorial judgments, not predicted results")
        if not SETTINGS.overview_model and not SETTINGS.blurbs_model:
            return ratings + ", and the page shows no AI-written text."
        text = ratings + "."
        if SETTINGS.overview_model:
            text += (f" Then {who(SETTINGS.overview_model)}, searches the web and writes the overview at the top (marked "
                     "AI Summary) about the matches of the days the page shows, on every service and in every competition, "
                     "so it reads the same whatever you choose to see.")
        if SETTINGS.blurbs_model:
            text += (f" For the cards, {who(SETTINGS.blurbs_model)}, searches the web for a blurb on each of the "
                     f"{story_candidates()} best-scored matches in the days the page shows.")
        each = "Each is" if SETTINGS.overview_model and SETTINGS.blurbs_model else "It is"
        return text + (f" {each} published only with a page its own search returned, linked beside it; a day without one "
                       "has none. The page shows no other AI-written text.")
    return ("The overview (marked AI Summary) and the match blurbs are written by Claude, Anthropic's AI model "
            f"({SETTINGS.model}), once a day, early in the morning; the midday and evening rebuilds update fixtures, "
            "broadcasters and scores but keep the morning's text. Blurbs link to their sources; one that rests only on "
            "ESPN's table, form and stage is labelled \"ESPN table and form\". Claude supplies a general overview, league "
            "context and a sourced blurb for every rated match. The overview focuses on the current day when possible and "
            "looks further ahead when needed. Blurbs with no available matches are hidden; excluded phrases within a "
            "relevant blurb are dimmed. One overview appears at the top; match-specific news stays with its match card or "
            "schedule row. Claude also rates each upcoming match for popularity (25%), expected gameplay (35%) and "
            "competitive impact (40%), without seeing the Outlook score; its ratings are editorial judgments, not "
            "predicted results.")


def story_candidates():
    """How many top matches get a researched blurb, shared with the generation settings."""
    return story_state.BLURB_CANDIDATES


def about_scores():
    """The footer's account of the Outlook score and the pick score, with the settings' proportions."""
    w = shares(SETTINGS.outlook["weights"])
    parts = [f"{OUTLOOK_WORDS[k]} ({w[k]}%)" for k in OUTLOOK_PARTS if w[k]]
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    b = SETTINGS.blend
    pick = shares({"interest": b["interest"], "league_priority": b["league_priority"]})
    text = ("The page's own Outlook score rates every match from ESPN's data alone, by the same arithmetic for each: "
            f"{listed}. Betting-market inputs come from the odds in ESPN's feed; the page shows no prices. ")
    d = [f"{t:g}" for t in SETTINGS.dots]
    dots = (f" Each match shows its pick score as one to five golden dots: two from {d[0]}, three from {d[1]}, four from "
            f"{d[2]} and five from {d[3]} out of 100.")
    if SETTINGS.ai:
        mix = shares({"ai": b["ai"], "outlook": b["outlook"]})
        return text + (f"A match's interest is {mix['ai']}% the AI rating and {mix['outlook']}% the Outlook score (the "
                       "Outlook score alone for a match without an AI rating), and its pick score is "
                       f"{pick['interest']}% interest and {pick['league_priority']}% league priority, adjustable in Lineup.") + dots
    return text + (f"A match's pick score is {pick['interest']}% the Outlook score and {pick['league_priority']}% league "
                   "priority, adjustable in Lineup.") + dots


def owner_pill_service(via):
    have = set(OWNER)
    for sid in sorted((x for x in via if x in have), key=SERVICE_RANK.index):
        return sid
    return ""


def pills_html(m):
    parts = []
    for i, o in enumerate(m.outlets):
        if not o.known:
            parts.append(f'<span class="pill pill--unk" data-i="{i}" title="{esc(UNRECOGNIZED_TITLE)}"><i class="dot"></i>'
                         f'{esc(o.label)}<span class="pill__free">not recognized</span></span>')
            continue
        sid = owner_pill_service(o.via)
        cls = "pill" + (f" pill--mine svc-{sid}" if sid else (" pill--free" if o.free else ""))
        free = '<span class="pill__free">free</span>' if o.free else ""
        parts.append(f'<span class="{cls}" data-i="{i}"><i class="dot"></i>{esc(o.label)}{free}</span>')
    if m.rule:
        sid = owner_pill_service(m.rule.via)
        cls = f"pill pill--rule svc-{sid}" if sid else "pill pill--rule pill--rule-off"
        parts.append(f'<span class="{cls}" data-rule="1"><i class="dot"></i>usually {esc(m.rule.label)}</span>')
    if m.hint:
        parts.append(f'<span class="pill pill--hint">{esc(m.hint)}</span>')
    if not parts:
        parts.append('<span class="pill pill--hint">No US broadcaster listed yet</span>')
    return "".join(parts)


SHORT = {"cable": "cable", "ota": "antenna", "free": "free app"}
UNRECOGNIZED_TITLE = "ESPN lists this channel, but the page doesn't know yet which services carry it"


def chip_html(m):
    if m.service in SHORT:
        return f'<span class="chip svc-{m.service}"><i class="dot"></i>{esc(m.outlet)}<span class="chip__via">{SHORT[m.service]}</span></span>'
    if m.service:
        label = SERVICES[m.service]
        via = "" if m.outlet == label else f'<span class="chip__via">{esc(m.outlet)}</span>'
        if m.basis == "rule":
            return f'<span class="chip chip--rule svc-{m.service}"><i class="dot"></i>{esc(label)}<span class="chip__via">usually</span></span>'
        return f'<span class="chip svc-{m.service}"><i class="dot"></i>{esc(label)}{via}</span>'
    if any(not o.known for o in m.outlets):
        # Possibly on the viewer's services: say what is known rather than "not in your lineup".
        return '<span class="chip chip--no chip--unk">Channel not recognized</span>'
    if m.outlets:
        return '<span class="chip chip--no">Not in your lineup</span>'
    return '<span class="chip chip--no chip--unk">Not listed yet</span>'


def ordinal(n):
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def form_html(form):
    if not form:
        return ""
    cells = "".join(f'<b class="f f-{c.lower()}"></b>' for c in form if c in "WDL")
    return f'<i class="form" title="Last five: {esc(form)}">{cells}</i>' if cells else ""


def record_html(record):
    """The season record as ESPN gives it (wins-draws-losses), with the words in a tooltip."""
    m = re.fullmatch(r"(\d+)-(\d+)-(\d+)", record or "")
    words = f' title="Won {m.group(1)}, drawn {m.group(2)}, lost {m.group(3)} this season"' if m else ""
    return f'<span class="team__rec"{words}>{esc(record)}</span>'


def team_sub(t):
    """Under a team's name: its table place and points, then its form. The season record rides along
    hidden, for a card with no blurb to show (a row keeps it in its Details panel)."""
    bits = []
    if t.rank:
        bits.append(ordinal(t.rank) + (f" of {t.size}" if t.size and t.size <= 6 else ""))
        if t.pts:
            bits.append(f"{t.pts} pt" if t.pts == "1" else f"{t.pts} pts")
    elif t.record:
        bits.append(t.record)
    txt = esc(" · ".join(bits))
    if t.rank and t.record:
        txt += f'<span class="team__more"> · </span>{record_html(t.record)}'
    f = form_html(t.form)
    if not txt and not f:
        return ""
    return f'<span class="team__sub"><span class="team__table">{txt}</span>{f}</span>'


def scorer_html(t):
    """The team's top scorer, hidden in a row (its Details panel has it) and shown on a card with no blurb."""
    if not t.leader or t.leader_goals in ("", "0"):
        return ""
    goals = "1 goal" if t.leader_goals == "1" else f"{t.leader_goals} goals"
    return f'<span class="team__scorer">Top scorer {esc(t.leader)}, {esc(goals)}</span>'


def team_html(t, cache, score_html):
    return (f'<span class="team">{logo_html(t, cache)}<span class="team__txt"><span class="team__name">{esc(t.name)}</span>'
            f'{team_sub(t)}{scorer_html(t)}</span>{score_html}</span>')


def gcal_link(m):
    # The placeholder date on an unconfirmed fixture is not a kickoff to put in a calendar.
    if not m.time_valid:
        return ""
    start = m.utc.strftime("%Y%m%dT%H%M%SZ")
    end = (m.utc + timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")
    # Calendar links are static, while the viewing route changes with each visitor's lineup.
    # Name the listed channels, not the owner's service, and preserve uncertainty for usual homes.
    channels = ", ".join(dict.fromkeys(outlet.label for outlet in m.outlets))
    coverage = (f"Listed channels: {channels}." if channels else
                f"Usually on {m.rule.label}; match listing not confirmed." if m.rule else "Check the broadcaster listing.")
    details = f"{m.comp}. {coverage}"
    q = urllib.parse.urlencode({"action": "TEMPLATE", "text": f"{m.home.name} v {m.away.name}", "dates": f"{start}/{end}",
                                "details": details, "location": m.venue})
    return "https://calendar.google.com/calendar/render?" + q


def goals_html(m):
    if not m.goals:
        return ""
    by = {}
    for minute, tid, who, note in m.goals:
        by.setdefault(tid, []).append(f"{who} {minute}" + (f" ({note})" if note else ""))
    parts = []
    for t in (m.home, m.away):
        if t.id in by:
            parts.append(f"<b>{esc(t.abbr or t.name)}</b> " + esc(", ".join(by[t.id])))
    return f'<div class="row__goals">{" &middot; ".join(parts)}</div>' if parts else ""


def detail_html(m, cache):
    cols = []
    for t in (m.home, m.away):
        facts = []
        if t.rank:
            grouped = len((STANDINGS.get(m.league) or {}).get("tables") or []) > 1   # name the group only when there are several
            facts.append(ordinal(t.rank) + (f" in {t.group}" if grouped and t.group else "") + (f", {t.pts} pt" + ("" if t.pts == "1" else "s") if t.pts else "") + (f", {t.record}" if t.record else ""))
        elif t.record:
            facts.append(f"Record {t.record}")
        if t.leader and t.leader_goals not in ("", "0"):
            facts.append(f"Top scorer {t.leader}, {t.leader_goals} goal" + ("" if t.leader_goals == "1" else "s"))
        cols.append(f'<div class="detail__team"><div><div class="detail__name">{esc(t.name)}</div><div class="detail__facts">{esc(" · ".join(facts)) if facts else "No table or scorer data yet."}</div></div></div>')
    facts = []
    if m.venue:
        facts.append(esc(m.venue))
    if m.attendance:
        facts.append(f"Attendance {m.attendance:,}")
    if m.stage:
        facts.append(esc(m.stage))
    recap = f'<p class="detail__recap">{esc(m.recap)}</p>' if m.recap else ""
    links = []
    if m.link:
        links.append(f'<a href="{esc(m.link)}" target="_blank" rel="noopener">ESPN match page</a>')
    if m.state == "pre" and m.time_valid:
        links.append(f'<a href="{esc(gcal_link(m))}" target="_blank" rel="noopener" title="Add to Google Calendar">Add to calendar</a>')
    if m.league in STANDINGS:
        links.append(f'<a href="#tables" class="detail__table" data-lg="{esc(m.league)}">League table</a>')
    venue_p = f'<p class="detail__venue">{" &middot; ".join(facts)}</p>' if facts else ""
    links_p = f'<p class="detail__links">{" ".join(links)}</p>' if links else ""
    return f'<div class="row__detail" hidden><div class="detail__grid">{"".join(cols)}</div>{venue_p}{recap}{links_p}</div>'


def league_logo_html(league, cache):
    url = LEAGUE_LOGOS.get(league, "")
    key = logo_key(url) if url else ""
    return f'<i class="lg l-{key}" aria-hidden="true"></i>' if key and key in cache else ""


def row_html(m, cache):
    t, ap, local = et_parts(m.utc)
    avail = "row--on" if m.service else "row--off"
    if m.service and m.basis == "rule":
        avail += " row--rule"
    tv = "1" if m.time_valid else "0"
    time_html = (f'<span class="t" data-t>{t}</span><span class="ap" data-ap>{ap}</span>' if m.time_valid
                 else '<span class="t t--tbd">TBD</span><span class="ap">time</span>')
    played = m.state != "pre" and not called_off(m.state, m.status)
    score_h, score_a = (f'<b class="score"{"" if played and t.score != "" else " hidden"}>{esc(t.score) if played else ""}</b>'
                        for t in (m.home, m.away))
    status = f'<span class="row__status"{"" if m.status else " hidden"}>{esc(m.status)}</span>'
    lglogo = league_logo_html(m.league, cache)
    league_badge = f'<div class="row__league" title="{esc(m.comp)}">{lglogo}</div>' if lglogo else ""
    meta = [f'<span class="comp">{esc(m.comp)}</span>']
    if m.stage:
        meta.append(f'<span class="stage">{esc(m.stage)}</span>')
    if m.venue:
        meta.append(f'<span class="venue">{esc(m.venue)}</span>')
    note = f'<div class="row__note">{esc(m.note)}</div>' if m.note else ""
    outlets_json = json.dumps([dict({"l": o.label, "v": o.via, "f": int(o.free), "e": int(o.es)}, **({} if o.known else {"u": 1}))
                               for o in m.outlets], ensure_ascii=False)
    rule_json = json.dumps({"l": m.rule.label, "v": m.rule.via}, ensure_ascii=False) if m.rule else ""
    parts = outlook_parts(m)
    parts_json = json.dumps({k: None if v is None else round(100 * v) for k, v in parts.items()})
    return (
        f'<li class="row {avail}" data-id="{esc(m.id)}" data-utc="{m.utc.strftime("%Y-%m-%dT%H:%M:%SZ")}" data-tv="{tv}" '
        f'data-lg="{esc(m.league)}" data-svc="{m.service or "none"}" data-basis="{m.basis}" data-score="{m.score}" '
        f'data-outlook="{outlook_score(parts):g}" data-outlook-parts="{esc(parts_json)}" '
        f'data-state="{m.state}" data-home="{esc(m.home.name)}" data-away="{esc(m.away.name)}" data-comp="{esc(m.comp)}" '
        + ('data-featured="1" ' if featured(m) else "") +
        f'data-outlet="{esc(m.outlet)}" data-hc="{m.home.color}" data-ac="{m.away.color}" data-o="{esc(outlets_json)}"' + (f' data-r="{esc(rule_json)}"' if rule_json else "") + '>'
        f'<div class="row__time">{league_badge}<div class="row__kickoff">{time_html}<span class="row__et" hidden></span><span class="row__until" hidden></span><span class="row__live" hidden>Live</span>{status}</div></div>'
        f'<div class="row__body">'
        f'<div class="row__teams">{team_html(m.home, cache, score_h)}<span class="vs">v</span>{team_html(m.away, cache, score_a)}</div>'
        f'<div class="row__meta">{"".join(meta)}</div>{goals_html(m)}{note}'
        f'<div class="pills">{pills_html(m)}<button type="button" class="more" aria-haspopup="dialog" aria-controls="match-dialog" '
        f'aria-label="Details: {esc(m.home.name)} v {esc(m.away.name)}">Details</button></div>'
        f'{detail_html(m, cache)}'
        f'</div>'
        f'<div class="row__watch">{chip_html(m)}</div>'
        f'</li>'
    )


def colors_html(home, away):
    h = f"#{home.color}" if home.color else "var(--line-strong)"
    a = f"#{away.color}" if away.color else "var(--line-strong)"
    return f'<div class="pick__colors" aria-hidden="true"><i style="background:{h}"></i><i style="background:{a}"></i></div>'


def tables_html(matches, cache, built_at):
    """Collapsed league tables for the leagues that play this week and publish standings, with the teams
    that play in the page's window (today and the three days after it) shaded."""
    out = []
    soon = story_state.window_end(built_at)
    for lg, info in LEAGUES.items():
        st = STANDINGS.get(lg)
        if not st or not any(m.league == lg for m in matches):
            continue
        playing = {t.id for m in matches if m.league == lg and m.state != "post" and m.utc < soon for t in (m.home, m.away)}
        lglogo = league_logo_html(lg, cache)
        groups = []
        for gname, rows in st["tables"]:
            body = ""
            for r in rows:
                k = logo_key(r["logo"]) if r["logo"] else ""
                logo = f'<i class="logo logo--sm l-{k}"></i>' if k and k in cache else ""
                cls = ' class="tbl__playing"' if r["id"] in playing else ""
                body += (f'<tr{cls}><td class="tbl__rank">{r["rank"] or ""}</td><td class="tbl__team">{logo}{esc(r["name"])}</td>'
                         f'<td>{esc(r["gp"])}</td><td>{esc(r["rec"])}</td><td>{esc(r["gd"])}</td><td class="tbl__pts">{esc(r["pts"])}</td></tr>')
            cap = f'<caption>{esc(gname)}</caption>' if gname else ""
            groups.append(f'<div class="tbl__wrap"><table class="tbl">{cap}<thead><tr><th></th><th>Team</th><th>P</th><th>W-D-L</th><th>GD</th><th>Pts</th></tr></thead><tbody>{body}</tbody></table></div>')
        out.append(f'<details class="fold tables__item" data-lg="{esc(lg)}"><summary><h3 class="bucket__h"><span>{lglogo}{esc(info["name"])}</span><span class="when"></span>'
                   f'<span class="bucket__count">{len(st["tables"])} {"tables" if len(st["tables"]) > 1 else "table"} <span class="caret"><span class="c">show &#9662;</span><span class="o">hide &#9652;</span></span></span></h3></summary>'
                   f'<div class="tables__groups">{"".join(groups)}</div></details>')
    return "".join(out)


LEAGUE_LOGOS = {}


def team_facts(t):
    table = ""
    if t.rank:
        table = ordinal(t.rank) + (f" of {t.size}" if t.size else "") + (f" in {t.group}" if t.group and t.size and t.size <= 8 else "")
        if t.pts:
            table += f", {t.pts} pts"
    return {k: v for k, v in dict(id=t.id, name=t.name, table=table, record=t.record, last_five=t.form,
                                     top_scorer=(f"{t.leader} ({t.leader_goals})" if t.leader and t.leader_goals not in ("", "0") else "")).items() if v}


def write_facts(path, matches, built_at, today):
    """Give Claude service-backed fixtures in the next 24 hours, with later candidates as fallback."""
    def routes(m):
        return sorted({sid for outlet in m.outlets for sid in outlet.via}
                      | (set(m.rule.via) if m.rule else set()))

    def entry(m):
        local = m.utc.astimezone(ET)
        return {k: v for k, v in dict(
            id=m.id,
            kickoff_utc=m.utc.isoformat(), time_confirmed=m.time_valid, state=m.state, league_id=m.league,
            source_url=f"https://www.espn.com/soccer/match/_/gameId/{m.id}",
            available_service_ids=routes(m), default_competition=shown_by_default(m),
            kickoff=(local.strftime("%a %b ") + str(local.day) + local.strftime(", %I:%M %p ET").replace(" 0", " ")) if m.time_valid else local.strftime("%a %b ") + str(local.day) + ", time TBD",
            competition=m.comp, stage=m.stage, venue=m.venue, note=m.note,
            home=team_facts(m.home), away=team_facts(m.away),
            watch_on=(SERVICES[m.service] + ("" if m.outlet == SERVICES[m.service] else f" ({m.outlet})") + (", usual home, channel not posted yet" if m.basis == "rule" else "")) if m.service else "",
            broadcasters=[o.label for o in m.outlets],
            stature=m.score).items() if v not in ("", [], None)}

    def result(m):
        team = {m.home.id: m.home.name, m.away.id: m.away.name}
        goals = [f"{who} {minute}" + (f" ({note})" if note else "") + f", for {team.get(tid, '?')}" for minute, tid, who, note in m.goals]
        return {k: v for k, v in dict(id=m.id, competition=m.comp, stage=m.stage, status=m.status,
                                      result=f"{m.home.name} {m.home.score}-{m.away.score} {m.away.name}", goals=goals).items()
                if v not in ("", [], None)}

    # Unknown coverage stays in the schedule, but cannot support a recommendation to watch.
    until = built_at + timedelta(hours=24)
    upcoming = [m for m in matches if m.state != "post" and routes(m)
                and not called_off(m.state, m.status)]
    near = [m for m in upcoming if built_at <= m.utc < until
            or (m.state == "in" and built_at - timedelta(hours=4) <= m.utc < built_at)]
    later = [m for m in upcoming if m.utc >= until]
    league_candidates = []
    for league in LEAGUES:
        candidates = sorted((m for m in near + later if m.league == league), key=lambda m: m.utc)
        if not candidates:
            continue
        # Keep each league's nearest window, even when other competitions fill the main digest.
        boundary = until if candidates[0].utc < until else candidates[0].utc + timedelta(hours=24)
        first_window = [m for m in candidates if m.utc < boundary]
        league_candidates.append(dict(league_id=league, competition=LEAGUES[league]["name"],
                                      matches=[entry(m) for m in first_window]))
    # The overview's fixtures, for every visitor whatever their services and competitions: every match
    # with known coverage in the page's window, today and the three days after it, which the AI rates.
    # (The page's top three came from the same matches until 9 October 2026; they follow each visitor's
    # lineup now.) later_if_needed doesn't serve: it stops at 20 fixtures, partway through a busy
    # Saturday morning.
    frame = near + [m for m in later if m.utc < story_state.window_end(built_at)]
    by_stature = lambda ms: sorted(ms, key=lambda m: (-m.score, m.utc))
    facts = {
        "date": today.isoformat(),
        "weekday": today.strftime("%A"),
        "built_at": built_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "focus_until": until.isoformat(),
        "owner_services": [SERVICES[k] for k in OWNER],
        "owner_service_ids": list(OWNER),   # recorded in story.json: the lineup the forecast was written for
        "leagues": [dict(league_id=league, competition=info["name"]) for league, info in LEAGUES.items()],
        "next_24_hours": [entry(m) for m in sorted(near, key=lambda m: m.utc)],
        "later_if_needed": [entry(m) for m in sorted(later, key=lambda m: m.utc)[:20]],
        "league_candidates": league_candidates,
        "overview_fixtures": [entry(m) for m in sorted(frame, key=lambda m: m.utc)],
        # What story.py needs to rank that frame as the page ranks its picks (web/app.js's pickValue), to
        # choose the matches whose blurbs to research: the blend's weights and each match's Outlook score.
        # No prompt carries it: the AI rates without seeing the Outlook score.
        "pick_inputs": {"blend": dict(SETTINGS.blend),
                        "outlook": {m.id: outlook_score(outlook_parts(m)) for m in frame}},
        "ranking_candidates": [dict(id=m.id, kickoff_utc=m.utc.isoformat(), competition=m.comp, league_id=m.league,
                                    source_url=f"https://www.espn.com/soccer/match/_/gameId/{m.id}",
                                    stage=m.stage, home=team_facts(m.home), away=team_facts(m.away))
                               for m in sorted(matches, key=lambda m: m.utc)
                               if m.state != "post" and not called_off(m.state, m.status)
                               and (m.utc >= built_at or (m.state == "in" and m.utc >= built_at - timedelta(hours=4)))],
    }
    played = [m for m in matches if m.state == "post" and m.utc.astimezone(ET).date() == today
              and not called_off(m.state, m.status) and m.score >= 85]
    if played:
        facts["played_today"] = [result(m) for m in by_stature(played)[:6]]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=1)


def build_page(matches, cache, built_at, failed, today, skipped=(), site_url=None):
    matches.sort(key=lambda m: (m.utc, -m.score, m.comp, m.home.name))
    # Static fallback: rows grouped by Eastern day, headed as the page script heads its days (Today and
    # Tomorrow with the full date in grey, a later day by its weekday with the rest of its date in grey).
    # The script regroups them by the viewer's clock and sports day.
    days = {}
    for m in matches:
        days.setdefault(m.utc.astimezone(ET).date(), []).append(m)
    static_sections = []
    for d, ms in sorted(days.items()):
        name = {today - timedelta(days=1): "Yesterday", today: "Today", today + timedelta(days=1): "Tomorrow"}.get(d)
        title, when = (name, d.strftime("%A, %B ") + str(d.day)) if name else (d.strftime("%A"), d.strftime("%B ") + str(d.day))
        static_sections.append(
            f'<section class="bucket" data-static="1"><h3 class="bucket__h"><span>{esc(title)}</span><span class="when">{esc(when)}</span>'
            f'<span class="bucket__count"></span></h3><ol class="rows">{"".join(row_html(m, cache) for m in ms)}</ol></section>')
    if not matches:
        static_sections.append('<p class="empty">No matches are scheduled in the fetched date range.</p>')

    # What the page counts before its script runs (the script recounts by the viewer's clock): the
    # window's matches, today and the three days after it.
    focus = [m for m in matches if m.state != "post" and shown_by_default(m)
             and built_at - timedelta(minutes=125) <= m.utc < story_state.window_end(built_at)]

    have_buttons = {k: (
        f'<button type="button" class="fpill svc-{k}" data-kind="have" data-key="{k}" aria-pressed="{"true" if k in OWNER else "false"}">'
        f'<span class="fpill__grip" aria-hidden="true">⠿</span><i class="dot"></i>{esc(v)}</button>') for k, v in SERVICES.items()}
    have_pills = "".join(have_buttons[k] for k in SERVICES if k in OWNER)
    have_off_pills = "".join(have_buttons[k] for k in sorted(SERVICES, key=lambda k: SERVICES[k].casefold()) if k not in OWNER)
    comps = []
    for lg, info in LEAGUES.items():
        n = sum(1 for m in matches if m.league == lg)
        if n:
            comps.append((info["name"], lg, n, bool(info.get("default_off"))))
    comps.sort(key=lambda c: (-c[2], c[0]))
    comp_buttons = {lg: (
        f'<button type="button" class="fpill" data-kind="comp" data-key="{esc(lg)}" data-short="{esc(LEAGUES[lg]["short"])}" data-default-off="{"1" if off else "0"}" aria-pressed="{"false" if off else "true"}"><span class="fpill__grip" aria-hidden="true">⠿</span>{league_logo_html(lg, cache)}{esc(name)}'
        f'<span class="fpill__n">{n}</span></button>') for name, lg, n, off in comps}
    comp_pills = "".join(comp_buttons[lg] for _, lg, _, off in comps if not off)
    comp_off_pills = "".join(comp_buttons[lg] for _, lg, _, off in sorted(comps, key=lambda c: c[0].casefold()) if off)

    lineup = "".join(
        f'<div class="svc svc-{k}" data-svc="{k}"><div class="svc__head"><i class="dot"></i><span class="svc__name">{esc(SERVICES[k])}</span>'
        f'<span class="svc__count" data-count>{sum(1 for m in focus if m.service == k)} in the next three days</span></div>'
        f'<p class="svc__desc" data-desc></p></div>' for k in OWNER)

    svc_meta = {"order": SERVICE_RANK, "name": SERVICES, "owner": OWNER,
                "leagues": {league: info["name"] for league, info in LEAGUES.items()}}
    used = set()
    for m in matches:
        used.update([m.home.logo_key, m.away.logo_key])
        if LEAGUE_LOGOS.get(m.league):
            used.add(logo_key(LEAGUE_LOGOS[m.league]))
    for lg, st in STANDINGS.items():
        if any(m.league == lg for m in matches):
            for _, rows in st["tables"]:
                used.update(logo_key(r["logo"]) for r in rows if r["logo"])
    used.discard("")
    logo_css = "".join(f'.l-{k}{{background-image:url("{v}")}}' for k, v in sorted(cache.items())
                       if k in used and '"' not in v and "\\" not in v)
    n_on = sum(1 for m in focus if m.service)
    failed_note = ""
    if failed:
        bad = sorted({LEAGUES[lg]["name"] for lg, _ in failed})
        failed_note = f"<p>ESPN did not return a usable schedule for {esc(', '.join(bad))} on at least one day of this build, so those fixtures may be missing.</p>"
    if skipped:
        bad = sorted({LEAGUES[entry["league"]]["name"] for entry in skipped})
        failed_note += (f"<p>{len(skipped)} unreadable fixture record{'s' if len(skipped) != 1 else ''} from "
                        f"{esc(', '.join(bad))} could not be included. Other fixtures remain available.</p>")
    built_et = built_at.astimezone(ET)
    page = (TEMPLATE
            .replace("@@LOGO_CSS@@", logo_css)
            .replace("@@SERVICE_META@@", json.dumps(svc_meta).replace("</", "<\\/"))
            .replace("@@SCORING@@", json.dumps({"blend": SETTINGS.blend, "dots": list(SETTINGS.dots)}).replace("</", "<\\/"))
            .replace("@@AI@@", "on" if SETTINGS.ai else "off")
            .replace("@@PRIORITY_HINT@@", esc(priority_hint()))
            .replace("@@ABOUT_AI@@", esc(about_ai()))
            .replace("@@ABOUT_SCORES@@", esc(about_scores()))
            .replace("@@BROWSE@@", discovery.navigation(site_url or SITE_URL))
            .replace("@@BUILT_ISO@@", built_at.strftime("%Y-%m-%dT%H:%M:%SZ"))
            .replace("@@INCOMPLETE@@", "1" if failed or skipped else "0")
            .replace("@@BUILT_ET@@", esc(built_et.strftime("%a %b ") + str(built_et.day) + built_et.strftime(", %I:%M %p ET").replace(" 0", " ")))
            .replace("@@N_ON@@", str(n_on))
            .replace("@@HAVE_PILLS@@", have_pills).replace("@@COMP_PILLS@@", comp_pills)
            .replace("@@HAVE_OFF_PILLS@@", have_off_pills).replace("@@COMP_OFF_PILLS@@", comp_off_pills)
            .replace("@@OUTLOOK@@", "".join(static_sections))
            .replace("@@TABLES@@", tables_html(matches, cache, built_at))
            .replace("@@LINEUP@@", lineup)
            .replace("@@FAILED@@", failed_note)
            .replace("@@OWNER_PROSE@@", esc(owner_prose())))
    return page


SITE_URL = "https://caparomula.github.io/soccer-outlook/"
SITE_TITLE = "Soccer Outlook — Soccer TV & Streaming Schedule"
SITE_DESCRIPTION = ("Find soccer matches on US TV and streaming services today and over the next three days. "
                    "Filter by your services and leagues, check kickoff times, and follow live scores.")


def public_site_url(value):
    """A public directory URL shared by canonical metadata and the sitemap."""
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError("site URL must be an absolute HTTPS URL without credentials, query or fragment")
    return value.rstrip("/") + "/"


def verification_meta(environ=None):
    """Optional public ownership tokens supplied by the site's webmaster accounts."""
    environ = os.environ if environ is None else environ
    tags = []
    for variable, name in (("GOOGLE_SITE_VERIFICATION", "google-site-verification"),
                           ("BING_SITE_VERIFICATION", "msvalidate.01")):
        value = environ.get(variable, "").strip()
        if value:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
                raise ValueError(f"{variable} must contain only the verification tag's content token")
            tags.append(f'<meta name="{name}" content="{esc(value)}">')
    return "\n".join(tags)


def indexnow_key(environ=None):
    """This public proof-of-ownership key is also served as a text file on the site."""
    environ = os.environ if environ is None else environ
    key = environ.get("INDEXNOW_KEY", "").strip()
    if not key:
        path = WEB_DIR / "indexnow-key.txt"
        key = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if key and not re.fullmatch(r"[A-Za-z0-9-]{8,128}", key):
        raise ValueError("INDEXNOW_KEY must be 8–128 letters, digits or hyphens")
    return key


DOCUMENT_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>@@SITE_TITLE@@</title>
<meta name="description" content="@@SITE_DESCRIPTION@@">
<link rel="canonical" href="@@SITE_URL@@">
<link rel="sitemap" type="application/xml" href="@@SITE_URL@@sitemap.xml">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Soccer Outlook">
<meta property="og:title" content="@@SITE_TITLE@@">
<meta property="og:description" content="@@SITE_DESCRIPTION@@">
<meta property="og:url" content="@@SITE_URL@@">
<meta name="twitter:card" content="summary">
@@VERIFICATION@@
<meta name="color-scheme" content="light dark">
<link rel="icon" type="image/png" sizes="64x64" href="favicon.png">
<style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}*,*::before,*::after{box-sizing:inherit}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""


def as_document(fragment, site_url=SITE_URL):
    """Wraps the page fragment in a complete HTML document, with the small reset the fragment
    expects: safe-area padding, no body margin, and [hidden] that wins over component display rules."""
    # Embedded fragments retain their original title for hosts; full pages own their head metadata.
    fragment = re.sub(r"\A<title>[^<]*</title>\s*", "", fragment, count=1)
    head = (DOCUMENT_HEAD.replace("@@SITE_TITLE@@", esc(SITE_TITLE))
            .replace("@@SITE_DESCRIPTION@@", esc(SITE_DESCRIPTION))
            .replace("@@VERIFICATION@@", verification_meta())
            .replace("@@SITE_URL@@", esc(public_site_url(site_url))))
    return head + fragment + "\n</body>\n</html>\n"


def sitemap(site_url, built_at, paths=()):
    """Include permanent public pages, while personal filters remain browser preferences."""
    modified = built_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = public_site_url(site_url)
    entries = "".join(f'  <url><loc>{esc(base + path)}</loc><lastmod>{modified}</lastmod></url>\n'
                      for path in ("", *paths))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + entries +
            '</urlset>\n')


# ----------------------------------------------------------------------------------------------
# Page sources stay separate for editing and are inlined into one generated HTML file.
# ----------------------------------------------------------------------------------------------
WEB_DIR = Path(__file__).resolve().parent / "web"
TEMPLATE = (WEB_DIR.joinpath("page.html").read_text(encoding="utf-8")
            .replace("@@STYLES@@", WEB_DIR.joinpath("styles.css").read_text(encoding="utf-8"))
            .replace("@@SCRIPT@@", "\n".join(WEB_DIR.joinpath(name).read_text(encoding="utf-8")
                                            for name in ("live.js", "app.js"))))


# ----------------------------------------------------------------------------------------------
UNKNOWN_OUTLETS = collections.Counter()   # broadcaster names ESPN listed that OUTLETS doesn't know


def audit(matches):
    """What this build couldn't map or can no longer vouch for, as lines for the run's report.

    These kinds of drift have made the page quietly wrong without breaking the build: ESPN using a
    broadcaster name rights.toml doesn't have ("USA Net", "Fandango"), whose matches then count as
    on no service; a competition's rights moving, so that its usual home (the Bundesliga's
    "usually ESPN+") no longer matches what ESPN lists; a season ending with its usual home
    unconfirmed for the next; and facts nobody has checked for months."""
    lines = [f"ESPN lists a broadcaster rights.toml doesn't have: {name!r}, on {n} match{'' if n == 1 else 'es'}. "
             f"Add it under [channels] (or as another name of a channel there) and to the services that carry it."
             for name, n in UNKNOWN_OUTLETS.most_common()]
    for lg, r in RIGHTS.leagues.items():
        u, name = r.usual, LEAGUES[lg]["name"]
        if not u:
            continue
        home_of = u.channel or "set club by club"
        if TODAY > u.until:
            lines.append(f"{name}: its usual home ({home_of}) was for {u.season} and lapsed on {u.until}, so the page "
                         f"no longer claims one. Confirm this season's home in rights.toml (leagues.{lg}).")
            continue
        left = (u.until - TODAY).days
        if left <= LAPSE_NOTICE_DAYS:
            lines.append(f"{name}: its usual home ({home_of}, {u.season}) lapses in {left} day{'' if left == 1 else 's'}, "
                         f"on {u.until}. Confirm next season's home in rights.toml (leagues.{lg}).")
        if u.by_home:
            missing = sorted({(m.home.id, m.home.name) for m in matches if m.league == lg and m.home.id not in u.by_home})
            for team_id, club in missing:
                lines.append(f"{name}: home club {club!r} (ESPN ID {team_id or 'missing'}) has no usual home in rights.toml "
                             f"(leagues.{lg}.by_home_team), so its home matches claim none. Add its ESPN team ID.")
        if not u.channel:
            continue
        listed = [m for m in matches if m.league == lg and m.outlets]
        hits = sum(1 for m in listed if any(o.label == u.channel for o in m.outlets))
        if len(listed) >= 5 and hits < 0.2 * len(listed):
            lines.append(f"{name} is assumed to be usually on {u.channel}, but only {hits} of its {len(listed)} listed "
                         f"matches are. Check its rights and update rights.toml (leagues.{lg}).")
    for what, when in RIGHTS.checked:
        age = (TODAY - when).days
        if age > STALE_AFTER_DAYS:
            lines.append(f"{what} was last checked {age} days ago, on {when}. Check it again and update its entry in rights.toml.")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="site/index.html")
    ap.add_argument("--site-url", type=public_site_url, default=SITE_URL,
                    help="public HTTPS directory URL for search metadata and sitemap (set for forks or custom domains)")
    ap.add_argument("--embed-images", action="store_true", help="embed images as data URIs instead of linking ESPN's server")
    ap.add_argument("--logos", default="logos.json", help="image cache for --embed-images, read and updated")
    ap.add_argument("--fragment", action="store_true", help="write the page body only, for hosts that add the document wrapper")
    ap.add_argument("--days-ahead", type=int, default=4, help="days of fixtures to fetch: the page shows today and the "
                    "three days after it (story_state.WINDOW_DAYS), and one more covers a page read after midnight before the next build")
    ap.add_argument("--days-back", type=int, default=1)
    ap.add_argument("--date", help="treat this Eastern date as today (testing)")
    ap.add_argument("--no-logos", action="store_true", help="no team or league images at all")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--facts", help="also write the facts story.py gives the model to this JSON file")
    ap.add_argument("--warnings", help="also write the mapping report to this file, one warning per line (empty when clean)")
    ap.add_argument("--report", help="write JSON source-completeness and publication status, including on a failed build")
    args = ap.parse_args()
    # Reject malformed deployment settings before making requests or writing output.
    head_extra = verification_meta() if not args.fragment else ""
    discovery_key = indexnow_key() if not args.fragment else ""

    write_build_report(args.report, dict(version=1, publishable=False, complete=False,
                                        reasons=["Build did not complete."]))

    built_at = datetime.now(timezone.utc)
    today = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else built_at.astimezone(ET).date()
    global TODAY
    TODAY = today
    days = [today + timedelta(days=i) for i in range(-args.days_back, args.days_ahead + 1)]

    quality = BuildQuality()
    merged, league_logos, failed = fetch_scoreboards(days, args.workers, quality)
    LEAGUE_LOGOS.update(league_logos)
    STANDINGS.update(fetch_standings(list(LEAGUES), args.workers))
    matches = interpret_all(merged, quality)
    report = quality.report(len(matches))
    if not report["publishable"]:
        write_build_report(args.report, report)
        print("FAIL " + " ".join(report["reasons"]), file=sys.stderr)
        return 3
    warnings = audit(matches)
    for w in warnings:
        # On GitHub Actions a ::warning:: line becomes an annotation on the run's page.
        print(("::warning title=Broadcaster mapping::" if os.environ.get("GITHUB_ACTIONS") else "WARN ") + w)
    if args.warnings:
        os.makedirs(os.path.dirname(os.path.abspath(args.warnings)), exist_ok=True)
        with open(args.warnings, "w", encoding="utf-8") as f:
            f.write("".join(w + "\n" for w in warnings))
    if warnings and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("### Broadcaster mapping\n\n" + "".join(f"- {w}\n" for w in warnings) + "\n")
    wanted = missing = 0
    if args.no_logos:
        cache = {}
    elif args.embed_images:
        cache = load_logo_cache(args.logos)
        wanted, missing = fetch_logos(matches, cache, args.workers, league_logos)
        save_logo_cache(args.logos, cache)
    else:
        cache = image_links(matches, league_logos)

    page = build_page(matches, cache, built_at, failed, today, quality.skipped_events, args.site_url)
    trimmed = ""
    if len(page.encode("utf-8")) > PAGE_BUDGET:
        # First without league logos, then also without logos for background leagues.
        slim = {k: v for k, v in cache.items() if not k.startswith("L")}
        page = build_page(matches, slim, built_at, failed, today, quality.skipped_events, args.site_url)
        trimmed = "league logos"
        if len(page.encode("utf-8")) > PAGE_BUDGET:
            keep = {t.logo_key for m in matches if LEAGUES[m.league]["tier"] < 3 for t in (m.home, m.away)}
            slim = {k: v for k, v in slim.items() if k in keep}
            page = build_page(matches, slim, built_at, failed, today, quality.skipped_events, args.site_url)
            trimmed = "league logos+tier-3 team logos"
    if not args.fragment:
        page = as_document(page, args.site_url)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    if not args.fragment:
        # A separate PNG supports Safari versions without SVG/data-URI favicons.
        output_dir = Path(args.out).resolve().parent
        output_dir.joinpath("favicon.png").write_bytes(WEB_DIR.joinpath("favicon.png").read_bytes())
        public_pages = discovery.pages(sys.modules[__name__], matches, cache, built_at, args.site_url,
                                       failed=failed, skipped=quality.skipped_events, head_extra=head_extra,
                                       schedule_day=today if args.date else None)
        for route, document in public_pages.items():
            destination = output_dir / route / "index.html"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(document, encoding="utf-8")
        output_dir.joinpath("sitemap.xml").write_text(sitemap(args.site_url, built_at, public_pages), encoding="utf-8")
        if discovery_key:
            output_dir.joinpath(f"{discovery_key}.txt").write_text(discovery_key + "\n", encoding="utf-8")
    if args.facts:
        write_facts(args.facts, matches, built_at, today)
    write_build_report(args.report, report)
    n_on = sum(1 for m in matches if m.service)
    image_mode = "none" if args.no_logos else ("embedded" if args.embed_images else "linked")
    print(f"OK matches={len(matches)} on_services={n_on} days={days[0]}..{days[-1]} images={image_mode} logos_new={wanted} logos_missing={missing} tables={len(STANDINGS)} "
          f"fetch_failures={len(failed)} skipped_events={len(quality.skipped_events)} mapping_warnings={len(warnings)} bytes={len(page.encode('utf-8'))} trimmed={trimmed or 'none'} built={built_at.astimezone(ET).strftime('%Y-%m-%d %H:%M ET')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
