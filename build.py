#!/usr/bin/env python3
"""Builds Soccer Outlook: a one-page, week-long soccer schedule that says which matches are on a
viewer's streaming services.

Data comes from ESPN's public scoreboard and standings APIs, one scoreboard request per league per
day (the API rejects date ranges for soccer). Each match's listed US broadcasters are mapped to
streaming services through OUTLETS; when ESPN has not listed broadcasters yet (common more than a
few days out) a per-league rule in LEAGUES names the league's usual US home, and the page marks
that basis as "usually" rather than "listed". The page carries every outlet's service mapping, so
the viewer's own lineup, chosen in the page and kept in that browser, decides what counts as
available; OWNER is the default lineup.

The page's own script re-buckets the matches by the viewer's clock ("Live now", "This morning",
"This afternoon", "This evening", "Tonight", "Tomorrow", "Later this week") and refreshes that
view every minute. A GitHub Actions job (.github/workflows/refresh.yml) runs this script several
times a day and publishes the result to GitHub Pages; the page warns when it is more than 30 hours
old.

By default the page links team and league images from ESPN's image server, which keeps it small.
--embed-images embeds them as data URIs instead (a page that must work with no network access),
cached in --logos between runs and trimmed to PAGE_BUDGET when the page runs large.

Usage: python3 build.py --out site/index.html [--days-ahead 9] [--days-back 1]
                        [--date YYYY-MM-DD] [--embed-images --logos logos.json] [--fragment] [--workers 6]

Exit status is non-zero when no fixtures could be fetched at all, or when more than half of the
scoreboard requests failed; partial failures are listed in the page footer and the summary line.
"""
import argparse
import base64
import concurrent.futures as cf
import gzip
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
# Streaming services a viewer can say they have. The order is the preference when a match is on
# several of them. OWNER is the page owner's lineup, the default for every viewer until they pick
# their own in the page; the choice stays in that viewer's browser.
# ----------------------------------------------------------------------------------------------
SERVICES = {
    "hbo": "HBO Max",
    "fox": "Fox One",
    "para": "Paramount+",
    "espn": "ESPN Unlimited",
    "espnplus": "ESPN Select (ESPN+)",
    "apple": "Apple TV",
    "usa": "USA Network",
    "peacock": "Peacock",
    "prime": "Prime Video",
    "netflix": "Netflix",
    "disney": "Disney+",
    "vix": "ViX",
    "fubo": "Fubo",
    "fsp": "Fox Soccer Plus",
    "bein": "beIN Sports",
    "fanatiz": "Fanatiz",
    "dazn": "DAZN",
    "cable": "Cable or live-TV bundle",
    "ota": "Local channels (antenna)",
    "free": "Free apps (Tubi, Roku, Victory+)",
}
SERVICE_RANK = list(SERVICES)
OWNER = ["hbo", "fox", "para", "espn", "apple", "usa", "prime", "netflix", "disney"]

# ----------------------------------------------------------------------------------------------
# ESPN broadcaster short names (lower case) -> the outlet's label and the services that carry it.
# Facts behind the mapping: Fox One includes FOX, FS1, FS2, BTN and Fox Deportes but not Fox
# Soccer Plus; ESPN Unlimited includes every ESPN network, ESPN on ABC and ESPN+, while ESPN
# Select is ESPN+ alone; TNT Sports' soccer streams on HBO Max; CBS matches stream on Paramount+
# Premium and NBC's on Peacock; ViX Premium streams TUDN and Univision's matches; a cable or
# live-TV bundle carries the cable channels and the local stations; "free" is a free app.
# ----------------------------------------------------------------------------------------------
def _o(label, via, free=False, es=False):
    return dict(label=label, via=via, free=free, es=es)


OUTLETS = {
    "espn+": _o("ESPN+", ["espn", "espnplus"]),
    "espn": _o("ESPN", ["espn", "cable"]),
    "espn2": _o("ESPN2", ["espn", "cable"]),
    "espnu": _o("ESPNU", ["espn", "cable"]),
    "espnews": _o("ESPNEWS", ["espn", "cable"]),
    "espn deportes": _o("ESPN Deportes", ["espn", "cable"], es=True),
    "accn": _o("ACC Network", ["espn", "cable"]),
    "secn": _o("SEC Network", ["espn", "cable"]),
    "abc": _o("ABC", ["espn", "ota", "cable"], free=True),
    "fs1": _o("FS1", ["fox", "cable"]),
    "fs2": _o("FS2", ["fox", "cable"]),
    "fox": _o("FOX", ["fox", "ota", "cable"], free=True),
    "fox deportes": _o("Fox Deportes", ["fox", "cable"], es=True),
    "btn": _o("Big Ten Network", ["fox", "cable"]),
    "fox soccer plus": _o("Fox Soccer Plus", ["fsp"]),
    "fsp": _o("Fox Soccer Plus", ["fsp"]),
    "fox sports app": _o("Fox Sports app", []),
    "paramount+": _o("Paramount+", ["para"]),
    "cbs": _o("CBS", ["para", "ota", "cable"], free=True),
    "cbssn": _o("CBS Sports Network", ["cable"]),           # plus Paramount+ for all-match leagues
    "cbs sports network": _o("CBS Sports Network", ["cable"]),
    "golazo": _o("CBS Sports Golazo Network", ["free"], free=True),
    "cbs sports golazo network": _o("CBS Sports Golazo Network", ["free"], free=True),
    "apple tv": _o("Apple TV", ["apple"]),
    "apple tv+": _o("Apple TV", ["apple"]),
    "mls season pass": _o("Apple TV", ["apple"]),
    "usa": _o("USA Network", ["usa", "cable"]),
    "usa network": _o("USA Network", ["usa", "cable"]),
    "nbc": _o("NBC", ["peacock", "ota", "cable"], free=True),
    "peacock": _o("Peacock", ["peacock"]),
    "cnbc": _o("CNBC", ["cable"]),
    "tele": _o("Telemundo", ["ota", "cable"], free=True, es=True),
    "telemundo": _o("Telemundo", ["ota", "cable"], free=True, es=True),
    "universo": _o("Universo", ["cable"], es=True),
    "hbo max": _o("HBO Max", ["hbo"]),
    "max": _o("HBO Max", ["hbo"]),
    "tnt": _o("TNT", ["hbo", "cable"]),
    "tbs": _o("TBS", ["hbo", "cable"]),
    "trutv": _o("truTV", ["hbo", "cable"]),
    "prime video": _o("Prime Video", ["prime"]),
    "amazon prime video": _o("Prime Video", ["prime"]),
    "netflix": _o("Netflix", ["netflix"]),
    "disney+": _o("Disney+", ["disney"]),
    "ion": _o("ION", ["ota", "cable"], free=True),
    "roku": _o("The Roku Channel", ["free"], free=True),
    "victory+": _o("Victory+", ["free"], free=True),
    "tubi": _o("Tubi", ["free"], free=True),
    "youtube": _o("YouTube", ["free"], free=True),
    "cw": _o("The CW", ["ota", "cable"], free=True),
    "tudn": _o("TUDN", ["vix", "cable"], es=True),
    "univision": _o("Univision", ["vix", "ota", "cable"], free=True, es=True),
    "unimas": _o("UniMás", ["vix", "ota", "cable"], free=True, es=True),
    "unimás": _o("UniMás", ["vix", "ota", "cable"], free=True, es=True),
    "vix": _o("ViX", ["vix"], es=True),
    "fubo": _o("Fubo", ["fubo"]),
    "fubo sports network": _o("Fubo Sports Network", ["fubo", "free"], free=True),
    "bein sports": _o("beIN Sports", ["bein"]),
    "bein sports en español": _o("beIN Sports en Español", ["bein"], es=True),
    "fanatiz": _o("Fanatiz", ["fanatiz"]),
    "dazn": _o("DAZN", ["dazn"]),
    "hulu": _o("Hulu", []),
}
# Leagues whose every match streams on Paramount+, so a CBS Sports Network listing is also a
# Paramount+ stream. For other leagues (the NWSL) that simulcast is not confirmed.
PARAMOUNT_EVERY_MATCH = {"ita.1", "eng.w.1", "uefa.champions", "uefa.europa", "uefa.europa.conf",
                         "uefa.wchampions", "sco.1", "eng.league_cup", "ita.coppa_italia", "eng.2"}

# ----------------------------------------------------------------------------------------------
# Leagues to track. tier: 1 marquee, 2 solid, 3 background. rule: the household service that
# carries every match of the league when ESPN lists nothing yet (basis "usually"); hint: where
# the league lives when it is not on any of the household's services. default_off: hidden until
# the viewer turns the competition pill on.
# ----------------------------------------------------------------------------------------------
LEAGUES = {
    "eng.1": dict(name="Premier League", tier=1, hint="NBC, USA Network or Peacock · channel posted a few days out"),
    "esp.1": dict(name="La Liga", tier=1, rule="espn", rule_outlet="ESPN+"),
    "ger.1": dict(name="Bundesliga", tier=1, rule="espn", rule_outlet="ESPN+"),
    "ita.1": dict(name="Serie A", tier=1, rule="para", rule_outlet="Paramount+"),
    "fra.1": dict(name="Ligue 1", tier=2, hint="beIN Sports"),
    "usa.1": dict(name="MLS", tier=2, rule="apple", rule_outlet="Apple TV"),
    "mex.1": dict(name="Liga MX", tier=2, hint="TUDN, Univision or ViX · a few clubs on FS1, FS2 or Fox Deportes"),
    "usa.nwsl": dict(name="NWSL", tier=2, hint="CBS or Paramount+, ESPN, Prime Video, ION or Victory+"),
    "eng.w.1": dict(name="Women's Super League", tier=2, rule="para", rule_outlet="Paramount+"),
    "uefa.champions": dict(name="Champions League", tier=1, rule="para", rule_outlet="Paramount+"),
    "uefa.europa": dict(name="Europa League", tier=2, rule="para", rule_outlet="Paramount+"),
    "uefa.europa.conf": dict(name="Conference League", tier=3, rule="para", rule_outlet="Paramount+"),
    "uefa.wchampions": dict(name="Women's Champions League", tier=2, rule="para", rule_outlet="Paramount+"),
    "uefa.nations": dict(name="Nations League", tier=2, hint="Fox Sports family · FS1/FS2 are in Fox One; Fox Soccer Plus, Fubo and Tubi are not"),
    "fifa.friendly": dict(name="Men's friendly", tier=2),
    "fifa.friendly.w": dict(name="Women's friendly", tier=2),
    "concacaf.nations.league": dict(name="Concacaf Nations League", tier=3, hint="Paramount+ has carried Concacaf; not confirmed for this match"),
    "eng.2": dict(name="Championship", tier=3, hint="Select matches on Paramount+ or CBS Sports Golazo"),
    "eng.fa": dict(name="FA Cup", tier=2, rule="espn", rule_outlet="ESPN+"),
    "eng.league_cup": dict(name="Carabao Cup", tier=2, rule="para", rule_outlet="Paramount+"),
    "esp.copa_del_rey": dict(name="Copa del Rey", tier=2, rule="espn", rule_outlet="ESPN+"),
    "ger.dfb_pokal": dict(name="DFB-Pokal", tier=2, rule="espn", rule_outlet="ESPN+"),
    "ita.coppa_italia": dict(name="Coppa Italia", tier=2, rule="para", rule_outlet="Paramount+"),
    "ned.1": dict(name="Eredivisie", tier=2, rule="espn", rule_outlet="ESPN+"),
    "por.1": dict(name="Primeira Liga", tier=3),
    "sco.1": dict(name="Scottish Premiership", tier=3, rule="para", rule_outlet="Paramount+"),
    "usa.usl.1": dict(name="USL Championship", tier=3, rule="espn", rule_outlet="ESPN+", default_off=True),
    "usa.usl.l1": dict(name="USL League One", tier=3, rule="espn", rule_outlet="ESPN+", default_off=True),
    "bra.1": dict(name="Brasileirão", tier=3, hint="Fanatiz or TV Globo Internacional"),
    "arg.1": dict(name="Liga Profesional (Argentina)", tier=3, hint="Fanatiz, ViX or TyC Sports · Paramount+ no longer confirmed"),
    "ksa.1": dict(name="Saudi Pro League", tier=3),
    "concacaf.champions": dict(name="Concacaf Champions Cup", tier=2),
    "usa.open": dict(name="U.S. Open Cup", tier=3),
    "caf.nations": dict(name="Africa Cup of Nations", tier=2, hint="beIN Sports"),
}

# How the forecast prose treats each league: its family, and whether its name takes "the".
LEAGUE_CATEGORY = {
    "eng.1": "big", "esp.1": "big", "ger.1": "big", "ita.1": "big", "fra.1": "big",
    "uefa.champions": "ucl", "uefa.europa": "ucl", "uefa.europa.conf": "ucl",
    "uefa.nations": "nat", "fifa.friendly": "nat", "fifa.friendly.w": "nat", "concacaf.nations.league": "nat",
    "caf.nations": "nat",
}
NO_ARTICLE = {"esp.1", "ita.1", "fra.1", "usa.1", "mex.1", "usa.usl.l1", "fifa.friendly", "fifa.friendly.w"}

MARQUEE_CLUBS = {
    "Arsenal", "Chelsea", "Liverpool", "Manchester City", "Manchester United", "Tottenham Hotspur",
    "Newcastle United", "Real Madrid", "Barcelona", "Atlético Madrid", "Bayern Munich",
    "Borussia Dortmund", "Bayer Leverkusen", "Paris Saint-Germain", "Marseille", "Juventus",
    "Internazionale", "Inter Milan", "AC Milan", "Napoli", "AS Roma", "Roma", "Ajax Amsterdam", "Ajax", "PSV Eindhoven",
    "PSV", "Feyenoord", "Benfica", "FC Porto", "Porto", "Sporting CP", "Celtic", "Rangers",
    "Inter Miami CF", "LAFC", "LA Galaxy", "Club América", "Guadalajara", "Cruz Azul", "Al Nassr",
    "Al Hilal", "Al Ittihad", "Flamengo", "Palmeiras", "Boca Juniors", "River Plate",
    "Orlando Pride", "Kansas City Current", "Washington Spirit", "Gotham FC",
}
MARQUEE_NATIONS = {
    "United States", "Mexico", "England", "Spain", "France", "Germany", "Italy", "Brazil", "Argentina",
    "Portugal", "Netherlands", "Belgium", "Croatia", "Canada", "Uruguay", "Colombia", "Japan",
    "Morocco", "Denmark", "Switzerland", "Norway", "Scotland", "Sweden", "Australia", "South Korea",
}
NATIONAL_LEAGUES = {"uefa.nations", "fifa.friendly", "fifa.friendly.w", "concacaf.nations.league", "caf.nations"}
TIER_BASE = {1: 100, 2: 60, 3: 30}


@dataclass
class Outlet:
    label: str
    via: list            # SERVICES keys that carry this outlet
    free: bool = False
    es: bool = False


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
    head_key: str = ""      # logo-cache key of the top scorer's headshot, when embedded
    head_url: str = ""
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


def fetch_scoreboards(days, workers):
    """Returns ({league: {event_id: event}}, {league: logo url}, [failed (league, day)])."""
    jobs = [(lg, day) for lg in LEAGUES for day in days]

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
            if data is None:
                failed.append((lg, day))
                continue
            for ev in data.get("events", []):
                merged[lg][ev["id"]] = ev
            for lo in (data.get("leagues") or [{}])[0].get("logos") or []:
                if "dark" not in (lo.get("rel") or []) and lo.get("href") and lg not in logos:
                    logos[lg] = lo["href"]
    return merged, logos, failed


STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings"
NO_TABLE = {"fifa.friendly", "fifa.friendly.w", "eng.fa", "eng.league_cup", "esp.copa_del_rey", "ger.dfb_pokal",
            "ita.coppa_italia", "usa.open"}


def _stat(entry, name):
    for st in entry.get("stats") or []:
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
            groups = data.get("children") or []
            if not groups and data.get("standings"):
                groups = [{"name": "", "standings": data["standings"]}]
            by_team, tables = {}, []
            for g in groups:
                gname = g.get("name") or ""
                entries = (g.get("standings") or {}).get("entries") or []
                rows = []
                for e in entries:
                    t = e.get("team") or {}
                    try:
                        rank = int(float(_stat(e, "rank") or 0))
                    except ValueError:
                        rank = 0
                    row = dict(id=str(t.get("id") or ""), name=t.get("displayName") or "", logo=((t.get("logos") or [{}])[0].get("href") or ""),
                               rank=rank, pts=_stat(e, "points"), rec=_stat(e, "overall"), gp=_stat(e, "gamesPlayed"),
                               gd=_stat(e, "pointDifferential"), form="", group=gname, size=len(entries), note=(e.get("note") or {}).get("description") or "")
                    rows.append(row)
                    if row["id"]:
                        by_team[row["id"]] = row
                rows.sort(key=lambda r: (r["rank"] or 999, r["name"]))
                tables.append((gname, rows))
            if by_team:
                out[lg] = {"by_team": by_team, "tables": tables}
    return out


STANDINGS = {}


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
    m = re.search(r"/headshots/soccer/players/full/(\d+)\.png", url)
    if m:
        return "h" + m.group(1)
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
    slug = ((comp.get("season") or {}).get("slug") or "")
    return ""


def luminance(hex6):
    """Relative luminance of a 6-digit hex color (WCAG 2 definition), 0 for black to 1 for white."""
    def channel(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(hex6[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def interpret(league, ev):
    comp = ev["competitions"][0]
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
    for c in comp["competitors"]:
        t = c["team"]
        url = t.get("logo") or ""
        team = Team(name=t.get("displayName") or t.get("name") or "?", abbr=(t.get("abbreviation") or "")[:4],
                    logo_key=logo_key(url) if url else "", logo_url=url, score=str(c.get("score") or ""),
                    winner=bool(c.get("winner")), id=str(t.get("id") or ""))
        team.form = (c.get("form") or "")[-5:]
        recs = c.get("records") or []
        team.record = (recs[0].get("summary") or "") if recs else ""
        for grp in c.get("leaders") or []:
            if grp.get("name") == "goals" and grp.get("leaders"):
                top = grp["leaders"][0]
                ath = top.get("athlete") or {}
                team.leader = ath.get("shortName") or ath.get("displayName") or ""
                team.leader_goals = top.get("displayValue") or ""
                team.head_url = ath.get("headshot") or ""
                team.head_key = logo_key(team.head_url) if team.head_url else ""
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
        return None

    st = comp.get("status") or ev.get("status") or {}
    stype = st.get("type") or {}
    state = stype.get("state") or "pre"
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
    notes = comp.get("notes") or []
    note = (notes[0].get("headline") or notes[0].get("text") or "") if notes else ""

    v = comp.get("venue") or ev.get("venue") or {}
    venue = v.get("fullName") or ""
    city = (v.get("address") or {}).get("city") or ""
    if city and city.lower() not in venue.lower():
        venue = f"{venue}, {city}" if venue else city

    # Broadcasters listed by ESPN for the US market, English first.
    listed = []
    for g in comp.get("geoBroadcasts") or []:
        if (g.get("market") or {}).get("type", "National") != "National":
            continue
        name = ((g.get("media") or {}).get("shortName") or "").strip()
        if name and name not in [n for n, _ in listed]:
            listed.append((name, g.get("lang") or "en"))
    outlets = []
    for name, lang in listed:
        o = OUTLETS.get(name.lower(), _o(name, []))
        via = list(o["via"])
        if name.lower() in ("cbssn", "cbs sports network") and league in PARAMOUNT_EVERY_MATCH:
            via.insert(0, "para")
        outlets.append(Outlet(label=o["label"], via=via, free=bool(o.get("free")), es=bool(o.get("es"))))
    outlets.sort(key=lambda o: o.es)
    rule = None
    if not outlets and info.get("rule_outlet"):
        ro = OUTLETS[info["rule_outlet"].lower()]
        rule = Outlet(label=ro["label"], via=list(ro["via"]))
    hint = info.get("hint", "") if not outlets and not rule else ""
    service, basis, outlet = evaluate(outlets, rule, set(OWNER))

    goals = []
    for d in comp.get("details") or []:
        if not d.get("scoringPlay"):
            continue
        who = (d.get("athletesInvolved") or [{}])[0]
        note = "pen" if d.get("penaltyKick") else ("og" if d.get("ownGoal") else "")
        goals.append(((d.get("clock") or {}).get("displayValue") or "", str((d.get("team") or {}).get("id") or ""),
                      who.get("shortName") or who.get("displayName") or "", note))
    heads = comp.get("headlines") or []
    recap = (heads[0].get("description") or "") if heads else ""
    try:
        attendance = int(comp.get("attendance") or 0)
    except (TypeError, ValueError):
        attendance = 0
    link = ""
    for l in ev.get("links") or []:
        if l.get("href"):
            link = l["href"]
            break

    score = TIER_BASE[info["tier"]]
    if league == "uefa.nations":
        m = re.match(r"Group ([A-D])", group)
        score = {"A": 100, "B": 55, "C": 30, "D": 20}.get(m.group(1), 40) if m else 40
    names = {home.name, away.name}
    if league in NATIONAL_LEAGUES:
        score += 30 * len(names & MARQUEE_NATIONS)
        if "United States" in names:
            score += 45
    else:
        score += 25 * len(names & MARQUEE_CLUBS)
    if state == "post" and status.lower() in ("canceled", "cancelled", "postponed"):
        score = 0

    return Match(id=ev["id"], utc=datetime.fromisoformat(ev["date"].replace("Z", "+00:00")),
                 time_valid=bool(comp.get("timeValid", True)), league=league, comp=info["name"], stage=stage,
                 note=note, home=home, away=away, venue=venue, state=state, status=status, outlets=outlets,
                 rule=rule, hint=hint, service=service, basis=basis, outlet=outlet, score=score,
                 goals=goals, recap=recap, attendance=attendance, link=link)


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


HEADSHOT_MIN_SCORE = 100


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
            if t.head_key and m.score >= HEADSHOT_MIN_SCORE:
                links[t.head_key] = cdn_url(t.head_url, 96, crop=True)
    for lg, url in league_logos.items():
        links[logo_key(url)] = cdn_url(url, 64)
    for st in STANDINGS.values():
        for _, rows in st["tables"]:
            for r in rows:
                if r["logo"]:
                    links.setdefault(logo_key(r["logo"]), cdn_url(r["logo"], 64))
    return links


def fetch_logos(matches, cache, workers, league_logos):
    """Downloads every team logo, league logo and (for marquee matches) top-scorer headshot not yet cached."""
    wanted = {}
    for m in matches:
        for t in (m.home, m.away):
            if t.logo_key and t.logo_key not in cache:
                wanted[t.logo_key] = t.logo_url
            if m.score >= HEADSHOT_MIN_SCORE and t.head_key and t.head_key not in cache:
                wanted[t.head_key] = t.head_url
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
        return f'<i class="{cls} l-{esc(team.logo_key)}" role="img" aria-label=""></i>'
    return f'<i class="{cls} logo--txt" aria-hidden="true">{esc(team.abbr[:3])}</i>'


def owner_pill_service(via):
    have = set(OWNER)
    for sid in sorted((x for x in via if x in have), key=SERVICE_RANK.index):
        return sid
    return ""


def pills_html(m):
    parts = []
    for i, o in enumerate(m.outlets):
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


def chip_html(m):
    if m.service in SHORT:
        return f'<span class="chip svc-{m.service}"><i class="dot"></i>{esc(m.outlet)}<span class="chip__via">{SHORT[m.service]}</span></span>'
    if m.service:
        label = SERVICES[m.service]
        via = "" if m.outlet == label else f'<span class="chip__via">{esc(m.outlet)}</span>'
        if m.basis == "rule":
            return f'<span class="chip chip--rule svc-{m.service}"><i class="dot"></i>{esc(label)}<span class="chip__via">usually</span></span>'
        return f'<span class="chip svc-{m.service}"><i class="dot"></i>{esc(label)}{via}</span>'
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


def team_sub(t):
    bits = []
    if t.rank:
        bits.append(ordinal(t.rank) + (f" of {t.size}" if t.size and t.size <= 6 else ""))
        if t.pts:
            bits.append(f"{t.pts} pt" if t.pts == "1" else f"{t.pts} pts")
    elif t.record:
        bits.append(t.record)
    txt = " · ".join(bits)
    f = form_html(t.form)
    if not txt and not f:
        return ""
    return f'<span class="team__sub">{esc(txt)}{f}</span>'


def team_html(t, cache, score_html):
    return (f'<span class="team">{logo_html(t, cache)}<span class="team__txt"><span class="team__name">{esc(t.name)}</span>'
            f'{team_sub(t)}</span>{score_html}</span>')


def gcal_link(m):
    start = m.utc.strftime("%Y%m%dT%H%M%SZ")
    end = (m.utc + timedelta(hours=2)).strftime("%Y%m%dT%H%M%SZ")
    where = SERVICES.get(m.service, "") if m.service else ""
    details = f"{m.comp}. " + (f"On {where}" + (f" ({m.outlet})" if m.outlet and m.outlet != where else "") + "." if where else "Check your services.")
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
        head = f'<i class="head l-{esc(t.head_key)}"></i>' if t.head_key and t.head_key in cache else ""
        facts = []
        if t.rank:
            facts.append(ordinal(t.rank) + (f" in {t.group}" if t.group and t.group.lower() not in ("overall",) else "") + (f", {t.pts} pt" + ("" if t.pts == "1" else "s") if t.pts else "") + (f", {t.record}" if t.record else ""))
        elif t.record:
            facts.append(f"Record {t.record}")
        if t.leader and t.leader_goals not in ("", "0"):
            facts.append(f"Top scorer {t.leader}, {t.leader_goals} goal" + ("" if t.leader_goals == "1" else "s"))
        cols.append(f'<div class="detail__team">{head}<div><div class="detail__name">{esc(t.name)}</div><div class="detail__facts">{esc(" · ".join(facts)) if facts else "No table or scorer data yet."}</div></div></div>')
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
        links.append(f'<a href="{esc(m.link)}" target="_blank" rel="noopener">Match page on ESPN</a>')
    if m.state == "pre":
        links.append(f'<a href="{esc(gcal_link(m))}" target="_blank" rel="noopener">Add to Google Calendar</a>')
    if m.league in STANDINGS:
        links.append(f'<a href="#tables" class="detail__table" data-lg="{esc(m.league)}">League table</a>')
    venue_p = f'<p class="detail__venue">{" &middot; ".join(facts)}</p>' if facts else ""
    links_p = f'<p class="detail__links">{" ".join(links)}</p>' if links else ""
    return f'<div class="row__detail" hidden><div class="detail__grid">{"".join(cols)}</div>{venue_p}{recap}{links_p}</div>'


def row_html(m, cache):
    t, ap, local = et_parts(m.utc)
    avail = "row--on" if m.service else "row--off"
    if m.service and m.basis == "rule":
        avail += " row--rule"
    tv = "1" if m.time_valid else "0"
    time_html = (f'<span class="t" data-t>{t}</span><span class="ap" data-ap>{ap}</span>' if m.time_valid
                 else '<span class="t t--tbd">TBD</span><span class="ap">time</span>')
    score_h = f'<b class="score">{esc(m.home.score)}</b>' if m.state != "pre" and m.home.score != "" else ""
    score_a = f'<b class="score">{esc(m.away.score)}</b>' if m.state != "pre" and m.away.score != "" else ""
    status = f'<span class="row__status">{esc(m.status)}</span>' if m.status else ""
    lgkey = logo_key(LEAGUE_LOGOS.get(m.league, "")) if LEAGUE_LOGOS.get(m.league) else ""
    lglogo = f'<i class="lg l-{lgkey}"></i>' if lgkey and lgkey in cache else ""
    meta = [f'<span class="comp">{lglogo}{esc(m.comp)}</span>']
    if m.stage:
        meta.append(f'<span class="stage">{esc(m.stage)}</span>')
    if m.venue:
        meta.append(f'<span class="venue">{esc(m.venue)}</span>')
    note = f'<div class="row__note">{esc(m.note)}</div>' if m.note else ""
    outlets_json = json.dumps([{"l": o.label, "v": o.via, "f": int(o.free), "e": int(o.es)} for o in m.outlets], ensure_ascii=False)
    rule_json = json.dumps({"l": m.rule.label, "v": m.rule.via}, ensure_ascii=False) if m.rule else ""
    return (
        f'<li class="row {avail}" data-id="{esc(m.id)}" data-utc="{m.utc.strftime("%Y-%m-%dT%H:%M:%SZ")}" data-tv="{tv}" '
        f'data-lg="{esc(m.league)}" data-svc="{m.service or "none"}" data-basis="{m.basis}" data-score="{m.score}" '
        f'data-state="{m.state}" data-home="{esc(m.home.name)}" data-away="{esc(m.away.name)}" data-comp="{esc(m.comp)}" '
        f'data-outlet="{esc(m.outlet)}" data-hc="{m.home.color}" data-ac="{m.away.color}" data-o="{esc(outlets_json)}"' + (f' data-r="{esc(rule_json)}"' if rule_json else "") + '>'
        f'<div class="row__time">{time_html}<span class="row__et" hidden></span><span class="row__until" hidden></span><span class="row__live" hidden>Live</span>{status}</div>'
        f'<div class="row__body">'
        f'<div class="row__teams">{team_html(m.home, cache, score_h)}<span class="vs">v</span>{team_html(m.away, cache, score_a)}</div>'
        f'<div class="row__meta">{"".join(meta)}</div>{goals_html(m)}{note}'
        f'<div class="pills">{pills_html(m)}<button type="button" class="more" aria-expanded="false">Details</button></div>'
        f'{detail_html(m, cache)}'
        f'</div>'
        f'<div class="row__watch">{chip_html(m)}</div>'
        f'</li>'
    )


def colors_html(home, away):
    h = f"#{home.color}" if home.color else "var(--line-strong)"
    a = f"#{away.color}" if away.color else "var(--line-strong)"
    return f'<div class="pick__colors" aria-hidden="true"><i style="background:{h}"></i><i style="background:{a}"></i></div>'


def pick_card_html(m, cache):
    t, ap, local = et_parts(m.utc)
    label = SERVICES[m.service]
    via = "" if m.outlet == label else f" · {esc(m.outlet)}"
    if m.service in SHORT:
        label, via = m.outlet, f" · {SHORT[m.service]}"
    usually = " (usually)" if m.basis == "rule" else ""
    subs = [re.sub(r"<[^>]+>", "", team_sub(x)).strip() for x in (m.home, m.away)]
    subline = f'<div class="pick__sub">{esc(subs[0] or "–")} <span class="pick__vs2">v</span> {esc(subs[1] or "–")}</div>' if any(subs) else ""
    return (
        f'<a class="pick svc-{m.service}" href="#outlook">'
        f'<div class="pick__when"><span class="t">{t}</span><span class="ap">{ap} ET · {local.strftime("%a")}</span></div>'
        f'<div class="pick__teams">{logo_html(m.home, cache, "logo logo--lg")}<span class="pick__vs">v</span>{logo_html(m.away, cache, "logo logo--lg")}</div>'
        f'<div class="pick__names">{esc(m.home.name)} v {esc(m.away.name)}</div>{subline}'
        f'<div class="pick__comp">{esc(m.comp)}</div>'
        f'<div class="pick__svc"><i class="dot"></i>{esc(label)}{via}{usually}</div>'
        f'{colors_html(m.home, m.away)}'
        f'</a>'
    )


def tables_html(matches, cache):
    """Collapsed league tables for the leagues that play this week and publish standings."""
    out = []
    for lg, info in LEAGUES.items():
        st = STANDINGS.get(lg)
        if not st or not any(m.league == lg for m in matches):
            continue
        soon = datetime.now(timezone.utc) + timedelta(days=3)
        playing = {t.id for m in matches if m.league == lg and m.state != "post" and m.utc <= soon for t in (m.home, m.away)}
        lgkey = logo_key(LEAGUE_LOGOS.get(lg, "")) if LEAGUE_LOGOS.get(lg) else ""
        lglogo = f'<i class="lg l-{lgkey}"></i>' if lgkey and lgkey in cache else ""
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


def build_page(matches, cache, built_at, failed, today):
    matches.sort(key=lambda m: (m.utc, -m.score, m.comp, m.home.name))
    # Static fallback: rows grouped by Eastern day. The page script regroups them by the viewer's clock.
    days = {}
    for m in matches:
        days.setdefault(m.utc.astimezone(ET).date(), []).append(m)
    static_sections = []
    for d, ms in sorted(days.items()):
        label = d.strftime("%A, %B ") + str(d.day)
        if d == today:
            label = "Today · " + label
        static_sections.append(
            f'<section class="bucket" data-static="1"><h3 class="bucket__h">{esc(label)}<span class="bucket__count"></span></h3>'
            f'<ol class="rows">{"".join(row_html(m, cache) for m in ms)}</ol></section>')

    horizon = [m for m in matches if m.service and m.state != "post"
               and today <= m.utc.astimezone(ET).date() <= today + timedelta(days=1)]
    picks = sorted(sorted(horizon, key=lambda m: (-m.score, m.utc))[:4], key=lambda m: m.utc)

    have_pills = "".join(
        f'<button type="button" class="fpill svc-{k}" data-kind="have" data-key="{k}" aria-pressed="{"true" if k in OWNER else "false"}">'
        f'<i class="dot"></i>{esc(v)}</button>' for k, v in SERVICES.items())
    comps = []
    for lg, info in LEAGUES.items():
        n = sum(1 for m in matches if m.league == lg)
        if n:
            comps.append((info["name"], lg, n, bool(info.get("default_off"))))
    comps.sort(key=lambda c: (-c[2], c[0]))
    comp_pills = "".join(
        f'<button type="button" class="fpill" data-kind="comp" data-key="{esc(lg)}" data-default-off="{"1" if off else "0"}" aria-pressed="{"false" if off else "true"}">{esc(name)}'
        f'<span class="fpill__n">{n}</span></button>' for name, lg, n, off in comps)

    lineup = "".join(
        f'<div class="svc svc-{k}" data-svc="{k}"><div class="svc__head"><i class="dot"></i><span class="svc__name">{esc(SERVICES[k])}</span>'
        f'<span class="svc__count" data-count>{sum(1 for m in matches if m.service == k and m.state != "post")} this week</span></div>'
        f'<p class="svc__desc" data-desc></p></div>' for k in OWNER)

    meta = {lg: {"name": info["name"], "cat": LEAGUE_CATEGORY.get(lg, "other"), "the": lg not in NO_ARTICLE,
                 "tier": info["tier"]} for lg, info in LEAGUES.items()}
    svc_meta = {"order": SERVICE_RANK, "name": SERVICES, "owner": OWNER}
    used = set()
    for m in matches:
        used.update([m.home.logo_key, m.away.logo_key])
        if m.score >= HEADSHOT_MIN_SCORE:
            used.update([m.home.head_key, m.away.head_key])
        if LEAGUE_LOGOS.get(m.league):
            used.add(logo_key(LEAGUE_LOGOS[m.league]))
    for lg, st in STANDINGS.items():
        if any(m.league == lg for m in matches):
            for _, rows in st["tables"]:
                used.update(logo_key(r["logo"]) for r in rows if r["logo"])
    used.discard("")
    logo_css = "".join(f'.l-{k}{{background-image:url("{v}")}}' for k, v in sorted(cache.items())
                       if k in used and '"' not in v and "\\" not in v)
    n_on = sum(1 for m in matches if m.service and m.state != "post")
    n_all = sum(1 for m in matches if m.state != "post")
    failed_note = ""
    if failed:
        bad = sorted({LEAGUES[lg]["name"] for lg, _ in failed})
        failed_note = f"<p>ESPN did not answer for {esc(', '.join(bad))} on at least one day of this build, so those fixtures may be missing.</p>"
    built_et = built_at.astimezone(ET)
    page = (TEMPLATE
            .replace("@@LOGO_CSS@@", logo_css)
            .replace("@@LEAGUE_META@@", json.dumps(meta).replace("</", "<\\/"))
            .replace("@@SERVICE_META@@", json.dumps(svc_meta).replace("</", "<\\/"))
            .replace("@@BUILT_ISO@@", built_at.strftime("%Y-%m-%dT%H:%M:%SZ"))
            .replace("@@BUILT_ET@@", esc(built_et.strftime("%a %b ") + str(built_et.day) + built_et.strftime(", %I:%M %p ET").replace(" 0", " ")))
            .replace("@@N_ON@@", str(n_on)).replace("@@N_ALL@@", str(n_all))
            .replace("@@PICKS@@", "".join(pick_card_html(m, cache) for m in picks))
            .replace("@@HAVE_PILLS@@", have_pills).replace("@@COMP_PILLS@@", comp_pills)
            .replace("@@OUTLOOK@@", "".join(static_sections))
            .replace("@@TABLES@@", tables_html(matches, cache))
            .replace("@@LINEUP@@", lineup)
            .replace("@@FAILED@@", failed_note))
    return page


DOCUMENT_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='15' fill='%2315744a'/%3E%3Cpath d='M16 9l6 4.4-2.3 7h-7.4L10 13.4z' fill='%23fff'/%3E%3C/svg%3E">
<style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}*,*::before,*::after{box-sizing:inherit}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""


def as_document(fragment):
    """Wraps the page fragment in a complete HTML document, with the small reset the fragment
    expects: safe-area padding, no body margin, and [hidden] that wins over component display rules."""
    return DOCUMENT_HEAD + fragment + "\n</body>\n</html>\n"


# ----------------------------------------------------------------------------------------------
# Page template: CSS and script are plain strings (no f-string braces to escape).
# ----------------------------------------------------------------------------------------------
TEMPLATE = r'''<title>Soccer Outlook</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&display=swap">
<style>
/* Layout: a broadcast rundown re-bucketed by the viewer's clock. Time rail left, match in the middle,
   the primary service chip right, and a row of broadcaster pills under every match. */
:root {
  --bg: #f2f4ef; --card: #ffffff; --fg: #15201a; --muted: #5b6a61; --line: #d6dcd4; --line-strong: #b9c3ba;
  --accent: #15744a; --amber: #b86b12; --amber-soft: #fbead3; --warn-bg: #fff3e0; --win: #2e9e5b; --loss: #c84b3c; --draw: #a8b1a9;
  --off-bg: #e9ece7; --off-fg: #6b776f; --pill-bg: #eceee9; --pill-fg: #55625a; --logo-pad: transparent;
  --svc-hbo: #5e46c9; --svc-fox: #1f5fbf; --svc-para: #0b6f8f; --svc-espn: #c2322a; --svc-apple: #3a3a3a;
  --svc-usa: #0e7c86; --svc-prime: #0f7ba8; --svc-netflix: #b3141c; --svc-disney: #1b3f8f; --svc-off: #8a948c;
  --svc-espnplus: #d8573a; --svc-peacock: #a0731a; --svc-vix: #b5367a; --svc-fubo: #d9731c; --svc-fsp: #4a7fd1;
  --svc-bein: #8e5aa8; --svc-fanatiz: #0e8a6a; --svc-dazn: #7a7a1a; --svc-cable: #7a6a4f; --svc-ota: #5f7f3f; --svc-free: #4f8a5f;
  --display: "Barlow Condensed", "Arial Narrow", "Helvetica Neue", Arial, sans-serif;
  --body: "Source Sans 3", "Segoe UI", Helvetica, Arial, sans-serif;
  --radius: 10px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f1512; --card: #182019; --fg: #e7ece6; --muted: #9ba89f; --line: #26312a; --line-strong: #3a4840;
  --accent: #3fb87a; --amber: #e5a24b; --amber-soft: #3a2a12; --warn-bg: #2e2412; --win: #4cc47f; --loss: #ff7b6f; --draw: #5a675e;
  --off-bg: #1f2823; --off-fg: #93a097; --pill-bg: #222c26; --pill-fg: #aab6ae; --logo-pad: rgba(255,255,255,0.08);
  --svc-hbo: #a391ff; --svc-fox: #6fa3ff; --svc-para: #49bbdb; --svc-espn: #ff7b6f; --svc-apple: #d0d0d0;
  --svc-usa: #4cc3cd; --svc-prime: #5cb8e6; --svc-netflix: #ff6b72; --svc-disney: #7da2ff; --svc-off: #7f8b82;
  --svc-espnplus: #ff9a80; --svc-peacock: #e0b35a; --svc-vix: #e986bd; --svc-fubo: #f0a060; --svc-fsp: #8fb4ff;
  --svc-bein: #c39de0; --svc-fanatiz: #5cc9a8; --svc-dazn: #c9c96a; --svc-cable: #c2ad88; --svc-ota: #9fc37a; --svc-free: #8ccf9a;
  color-scheme: dark;
} }
:root[data-theme="dark"] {
  --bg: #0f1512; --card: #182019; --fg: #e7ece6; --muted: #9ba89f; --line: #26312a; --line-strong: #3a4840;
  --accent: #3fb87a; --amber: #e5a24b; --amber-soft: #3a2a12; --warn-bg: #2e2412; --win: #4cc47f; --loss: #ff7b6f; --draw: #5a675e;
  --off-bg: #1f2823; --off-fg: #93a097; --pill-bg: #222c26; --pill-fg: #aab6ae; --logo-pad: rgba(255,255,255,0.08);
  --svc-hbo: #a391ff; --svc-fox: #6fa3ff; --svc-para: #49bbdb; --svc-espn: #ff7b6f; --svc-apple: #d0d0d0;
  --svc-usa: #4cc3cd; --svc-prime: #5cb8e6; --svc-netflix: #ff6b72; --svc-disney: #7da2ff; --svc-off: #7f8b82;
  --svc-espnplus: #ff9a80; --svc-peacock: #e0b35a; --svc-vix: #e986bd; --svc-fubo: #f0a060; --svc-fsp: #8fb4ff;
  --svc-bein: #c39de0; --svc-fanatiz: #5cc9a8; --svc-dazn: #c9c96a; --svc-cable: #c2ad88; --svc-ota: #9fc37a; --svc-free: #8ccf9a;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--fg); font-family: var(--body); font-size: 16px; line-height: 1.45; margin: 0; }
.wrap { max-width: 1000px; margin: 0 auto; padding-inline: 16px; padding-block: 20px 56px; }
h1, h2, h3, h4 { font-family: var(--display); font-weight: 700; letter-spacing: 0.01em; text-wrap: balance; margin: 0; }
a { color: inherit; }
.t { font-family: var(--display); font-weight: 700; font-variant-numeric: tabular-nums; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--svc); flex: none; display: inline-block; }
.svc-hbo { --svc: var(--svc-hbo); } .svc-fox { --svc: var(--svc-fox); } .svc-para { --svc: var(--svc-para); }
.svc-espn { --svc: var(--svc-espn); } .svc-apple { --svc: var(--svc-apple); } .svc-usa { --svc: var(--svc-usa); }
.svc-prime { --svc: var(--svc-prime); } .svc-netflix { --svc: var(--svc-netflix); } .svc-disney { --svc: var(--svc-disney); }
.svc-espnplus { --svc: var(--svc-espnplus); } .svc-peacock { --svc: var(--svc-peacock); } .svc-vix { --svc: var(--svc-vix); }
.svc-fubo { --svc: var(--svc-fubo); } .svc-fsp { --svc: var(--svc-fsp); } .svc-bein { --svc: var(--svc-bein); }
.svc-fanatiz { --svc: var(--svc-fanatiz); } .svc-dazn { --svc: var(--svc-dazn); } .svc-cable { --svc: var(--svc-cable); }
.svc-ota { --svc: var(--svc-ota); } .svc-free { --svc: var(--svc-free); }
.svc-off, .fpill--none { --svc: var(--svc-off); }

/* Header */
.hdr { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px 24px; padding-bottom: 14px; border-bottom: 2px solid var(--fg); }
.hdr__eyebrow { font-family: var(--display); font-weight: 600; text-transform: uppercase; letter-spacing: 0.12em; font-size: 13px; color: var(--muted); }
.hdr h1 { font-size: clamp(34px, 6vw, 56px); line-height: 1; margin-top: 2px; }
.hdr__tally { text-align: right; }
.hdr__tally .big { font-family: var(--display); font-size: 40px; font-weight: 700; line-height: 1; color: var(--accent); }
.hdr__tally .small { font-size: 13px; color: var(--muted); max-width: 22ch; }
.forecast { margin: 14px 0 0; max-width: 68ch; font-size: 17px; }
.forecast p { margin: 0 0 6px; }
.forecast b { font-weight: 600; }
.fresh { margin: 6px 0 0; font-size: 13px; color: var(--muted); }
.stale { margin: 12px 0 0; padding: 10px 12px; border-radius: 8px; background: var(--warn-bg); border: 1px solid var(--amber); font-size: 14px; }

/* Next up */
.nextup { margin-top: 18px; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 6px 18px; align-items: center; background: var(--card); border: 1px solid var(--line); border-left: 5px solid var(--accent); border-radius: var(--radius); padding: 12px 16px; }
.nextup--live { border-left-color: var(--amber); }
.nextup__status { font-family: var(--display); font-weight: 700; font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); }
.nextup--live .nextup__status { color: var(--amber); }
.nextup__count { font-family: var(--display); font-weight: 700; font-size: 38px; line-height: 1; font-variant-numeric: tabular-nums; min-width: 5ch; }
.nextup__match { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; min-width: 0; }
.nextup__names { font-weight: 600; font-size: 17px; }
.nextup__meta { color: var(--muted); font-size: 14px; }
.nextup__svc { display: inline-flex; align-items: center; gap: 6px; font-family: var(--display); font-weight: 600; font-size: 14px; letter-spacing: 0.03em; color: var(--svc); }
.nextup__left { display: flex; flex-direction: column; gap: 4px; }

/* Picks */
.sec { margin-top: 32px; }
.sec__h { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.sec__h h2 { font-size: 26px; text-transform: uppercase; letter-spacing: 0.04em; }
.sec__h p { margin: 0; color: var(--muted); font-size: 14px; }
.picks { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.picks:empty::after { content: "Nothing on your services in the next two days."; color: var(--muted); font-size: 14px; }
.pick { display: block; text-decoration: none; background: var(--card); border: 1px solid var(--line); border-top: 4px solid var(--svc); border-radius: var(--radius); padding: 14px 14px 12px; min-width: 0; }
.pick:hover, .pick:focus-visible { border-color: var(--svc); outline: none; }
.pick__when { display: flex; align-items: baseline; gap: 6px; }
.pick__when .t { font-size: 30px; line-height: 1; }
.pick__when .ap { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.pick__teams { display: flex; align-items: center; gap: 10px; margin: 12px 0 8px; }
.pick__vs { color: var(--muted); font-family: var(--display); font-size: 18px; }
.pick__names { font-weight: 600; line-height: 1.25; }
.pick__comp { color: var(--muted); font-size: 14px; margin-top: 2px; }
.pick__sub { color: var(--muted); font-size: 12.5px; margin-top: 2px; font-variant-numeric: tabular-nums; }
.pick__vs2 { opacity: 0.6; }
.pick__colors { height: 4px; display: flex; border-radius: 2px; overflow: hidden; margin-top: 10px; gap: 2px; }
.pick__colors i { flex: 1; display: block; }
.pick__svc { margin-top: 10px; display: inline-flex; align-items: center; gap: 7px; font-family: var(--display); font-weight: 600; font-size: 15px; letter-spacing: 0.03em; color: var(--svc); }

/* Controls */
.controls { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--bg); padding-block: 8px; margin-top: 26px; border-bottom: 1px solid var(--line); display: flex; flex-direction: column; gap: 8px; }
.controls__bar { gap: 8px 10px; }
.fbtn { appearance: none; display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line-strong); background: transparent; color: var(--fg); border-radius: 999px; padding: 6px 12px; font: 600 14px var(--body); cursor: pointer; white-space: nowrap; max-width: 100%; }
.fbtn__sum { color: var(--muted); font-weight: 400; overflow: hidden; text-overflow: ellipsis; max-width: 46vw; }
.fbtn__sum:not(:empty)::before { content: "\00b7"; margin-right: 6px; }
.fbtn__caret { color: var(--muted); font-size: 12px; }
.fbtn[aria-expanded="true"] { background: var(--card); }
.fbtn[aria-expanded="true"] .fbtn__caret { transform: rotate(180deg); }
.fbtn:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.drawer { display: flex; flex-direction: column; gap: 8px; padding: 10px 0 8px; border-bottom: 1px solid var(--line); }
.drawer__foot { justify-content: flex-end; }
@media (pointer: coarse) {
  .fpill { padding: 7px 12px; font-size: 14px; }
  .fbtn, .seg button { padding: 8px 14px; }
  .more { padding: 6px 8px; font-size: 13px; }
}
.drawer__hint { margin: -2px 0 2px; font-size: 12.5px; color: var(--muted); max-width: 70ch; }
.controls__row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.controls__lbl { font-family: var(--display); font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-right: 4px; min-width: 76px; }
.seg { display: inline-flex; border: 1px solid var(--line-strong); border-radius: 999px; overflow: hidden; }
.seg button { appearance: none; background: transparent; border: 0; color: var(--fg); font: 600 14px var(--body); padding: 7px 14px; cursor: pointer; }
.seg button[aria-pressed="true"] { background: var(--fg); color: var(--bg); }
.seg button:focus-visible, .fpill:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.fpill { appearance: none; display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line-strong); background: var(--card); color: var(--fg); border-radius: 999px; padding: 4px 10px; font: 600 13px var(--body); cursor: pointer; }
.fpill .dot { width: 8px; height: 8px; }
.fpill__n { color: var(--muted); font-weight: 400; font-variant-numeric: tabular-nums; }
.fpill[aria-pressed="false"] { background: transparent; color: var(--muted); border-style: dashed; }
.fpill[aria-pressed="false"] .dot { opacity: 0.35; }
.fpill[aria-pressed="false"] .fpill__n { text-decoration: line-through; }
.controls .tiny { font-size: 12px; color: var(--muted); margin-left: auto; }
.controls button.link { appearance: none; background: none; border: 0; color: var(--muted); font: inherit; font-size: 12px; text-decoration: underline; cursor: pointer; padding: 0; }

/* Outlook buckets and rows */
.bucket { margin-top: 22px; }
.bucket__h { font-size: 21px; text-transform: uppercase; letter-spacing: 0.06em; display: flex; align-items: baseline; gap: 12px; padding-bottom: 6px; border-bottom: 1px solid var(--line-strong); }
.bucket__h .when { font-family: var(--body); font-weight: 400; font-size: 14px; color: var(--muted); letter-spacing: 0; text-transform: none; }
.bucket__count { margin-left: auto; font-family: var(--body); font-weight: 400; font-size: 13px; color: var(--muted); letter-spacing: 0; text-transform: none; white-space: nowrap; }
.bucket--live .bucket__h { color: var(--amber); border-color: var(--amber); }
.bucket--now .bucket__h { color: var(--accent); border-color: var(--accent); }
.dayhead { font-family: var(--display); font-weight: 600; font-size: 16px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); margin-top: 14px; padding: 4px 0; border-bottom: 1px dashed var(--line); }
.rows { list-style: none; margin: 0; padding: 0; }
.row { display: grid; grid-template-columns: 76px minmax(0, 1fr) auto; gap: 8px 14px; align-items: start; padding: 10px 0; border-bottom: 1px solid var(--line); }
.row--off { color: var(--muted); }
.row--off .team__name { color: var(--muted); }
.row--off .logo { filter: grayscale(0.6); opacity: 0.75; }
.row__time { display: flex; flex-direction: column; align-items: flex-start; line-height: 1; padding-top: 2px; }
.row__time .t { font-size: 26px; color: var(--fg); }
.row--off .row__time .t { color: var(--muted); }
.t--tbd { font-size: 20px; letter-spacing: 0.04em; }
.row__time .ap { font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin-top: 3px; }
.row__et { font-size: 11.5px; color: var(--muted); margin-top: 4px; font-variant-numeric: tabular-nums; }
.row__live, .row__status { margin-top: 6px; font-family: var(--display); font-weight: 700; font-size: 12px; letter-spacing: 0.1em; text-transform: uppercase; padding: 2px 6px; border-radius: 4px; }
.row__live { color: var(--amber); background: var(--amber-soft); }
.row__status { color: var(--muted); background: var(--off-bg); }
.row[data-state="post"] .row__time .t, .row[data-state="post"] .row__time .ap { opacity: 0.6; }
.row__body { min-width: 0; }
.row__teams { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
.team { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.team__txt { display: flex; flex-direction: column; min-width: 0; line-height: 1.2; }
.team__name { font-weight: 600; font-size: 17px; }
.team__sub { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; margin-top: 2px; }
.form { display: inline-flex; gap: 2px; }
.f { width: 8px; height: 8px; border-radius: 2px; background: var(--draw); display: inline-block; }
.f-w { background: var(--win); } .f-l { background: var(--loss); }
.lg { width: 16px; height: 16px; display: inline-block; background: center / contain no-repeat; vertical-align: -3px; margin-right: 5px; }
.logo--sm { width: 18px; height: 18px; vertical-align: -4px; margin-right: 6px; border-radius: 3px; }
.row__goals { margin-top: 4px; font-size: 13px; color: var(--muted); }
.row__goals b { color: var(--fg); font-weight: 600; }
.row__until { font-size: 11.5px; color: var(--accent); margin-top: 4px; font-variant-numeric: tabular-nums; }
.more { appearance: none; border: 0; background: transparent; color: var(--muted); font: 600 12px var(--body); cursor: pointer; padding: 2px 4px; margin-left: auto; text-decoration: underline dotted; border-radius: 4px; }
.more:hover, .more:focus-visible { color: var(--fg); outline: none; }
.more[aria-expanded="true"] { color: var(--fg); }
.row__detail { margin-top: 8px; padding: 10px 12px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; font-size: 13.5px; }
.detail__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 8px 16px; }
.detail__team { display: flex; gap: 10px; align-items: center; min-width: 0; }
.detail__name { font-weight: 600; }
.detail__facts { color: var(--muted); }
.head { width: 40px; height: 40px; border-radius: 50%; background: var(--off-bg) center / cover no-repeat; flex: none; }
.detail__venue, .detail__recap, .detail__links { margin: 8px 0 0; color: var(--muted); }
.detail__recap { color: var(--fg); }
.detail__links a { margin-right: 14px; }
.row--off .detail__name { color: var(--fg); }
.score { font-family: var(--display); font-size: 20px; font-weight: 700; margin-left: 2px; font-variant-numeric: tabular-nums; }
.vs { color: var(--muted); font-family: var(--display); font-size: 16px; }
.logo { width: 32px; height: 32px; flex: none; display: inline-block; background: var(--logo-pad) center / contain no-repeat; border-radius: 6px; }
.logo--lg { width: 44px; height: 44px; }
.logo--txt { background: var(--off-bg); color: var(--off-fg); font: 700 11px var(--display); display: inline-flex; align-items: center; justify-content: center; letter-spacing: 0.04em; }
.row__meta { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 14px; color: var(--muted); margin-top: 3px; }
.comp { font-weight: 600; color: var(--fg); }
.row--off .comp { color: var(--muted); }
.stage::before, .venue::before { content: "\00b7"; margin-right: 10px; }
.row__note { margin-top: 5px; font-size: 14px; max-width: 62ch; color: var(--muted); }
.pills { display: flex; flex-wrap: wrap; gap: 4px 6px; margin-top: 6px; }
.pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; letter-spacing: 0.01em; padding: 2px 9px; border-radius: 999px; background: var(--pill-bg); color: var(--pill-fg); white-space: nowrap; line-height: 1.5; }
.pill .dot { width: 7px; height: 7px; }
.pill--mine { background: color-mix(in srgb, var(--svc) 14%, transparent); color: var(--svc); }
.pill--rule { background: transparent; border: 1px dashed var(--svc); color: var(--svc); }
.pill--rule-off { border-color: var(--line-strong); color: var(--pill-fg); }
.pill:not(.pill--mine):not(.pill--rule) .dot, .pill--rule-off .dot { display: none; }
.pill--hint { white-space: normal; font-weight: 400; font-style: italic; }
.pill__free { font-weight: 400; opacity: 0.8; }
.pill__free::before { content: "\00b7"; margin-right: 5px; }
.row__watch { justify-self: end; padding-top: 4px; }
.chip { display: inline-flex; align-items: center; gap: 7px; font-family: var(--display); font-weight: 600; font-size: 15px; letter-spacing: 0.03em; padding: 5px 11px 5px 9px; border-radius: 999px; border: 1px solid var(--svc); color: var(--svc); white-space: nowrap; }
.chip__via { color: var(--muted); font-weight: 600; }
.chip__via::before { content: "\00b7"; margin-right: 6px; }
.chip--rule { border-style: dashed; }
.chip--no { --svc: var(--off-fg); background: var(--off-bg); border-color: transparent; }
.chip--unk { background: transparent; border: 1px dashed var(--line-strong); }
details.fold { margin-top: 22px; }
details.fold summary { cursor: pointer; list-style: none; }
details.fold summary::-webkit-details-marker { display: none; }
details.fold summary .bucket__h { color: var(--muted); border-bottom-style: dashed; }
details.fold summary .caret { font-family: var(--body); font-size: 13px; letter-spacing: 0; text-transform: none; font-weight: 400; }
details.fold[open] summary .caret .c, details.fold:not([open]) summary .caret .o { display: none; }
.empty { color: var(--muted); font-size: 14px; padding: 14px 0; }

/* Misses */
.misses { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
.misses:empty::after { content: "Nothing notable is out of reach in the next two days."; color: var(--muted); font-size: 14px; }
.miss { display: flex; gap: 12px; align-items: flex-start; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: 12px; min-width: 0; }
.miss__teams { display: flex; align-items: center; gap: 6px; flex: none; }
.miss__vs { color: var(--muted); font-family: var(--display); font-size: 14px; }
.miss__names { font-weight: 600; line-height: 1.25; }
.miss__time { font-weight: 400; color: var(--muted); font-size: 14px; white-space: nowrap; }
.miss__where { color: var(--muted); font-size: 13px; margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; }

/* Lineup */
.lineup { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.svc { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--svc); border-radius: var(--radius); padding: 12px 14px; min-width: 0; }
.svc__head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.svc__name { font-family: var(--display); font-weight: 700; font-size: 20px; letter-spacing: 0.02em; }
.svc__count { margin-left: auto; font-size: 12px; color: var(--svc); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }
.svc__desc { margin: 6px 0 0; font-size: 14px; color: var(--muted); min-height: 1.4em; }
.svc--quiet .svc__count { color: var(--muted); }

/* Tables */
.tables__item { margin-top: 10px; }
.tables__item summary .bucket__h { font-size: 18px; }
.tables__groups { display: flex; flex-direction: column; gap: 14px; margin-top: 8px; }
.tbl__wrap { overflow-x: auto; }
.tbl { border-collapse: collapse; width: 100%; font-size: 13.5px; font-variant-numeric: tabular-nums; }
.tbl caption { text-align: left; font-family: var(--display); font-weight: 600; letter-spacing: 0.04em; color: var(--muted); padding: 4px 0; }
.tbl th { text-align: left; font-weight: 600; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; padding: 4px 8px 4px 0; border-bottom: 1px solid var(--line-strong); }
.tbl td { padding: 4px 8px 4px 0; border-bottom: 1px solid var(--line); white-space: nowrap; }
.tbl__rank { color: var(--muted); width: 2ch; }
.tbl__team { font-weight: 600; }
.tbl__pts { font-weight: 700; }
.tbl__playing td { background: color-mix(in srgb, var(--accent) 9%, transparent); }

/* Footer */
.foot { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line-strong); font-size: 13.5px; color: var(--muted); }
.foot p { margin: 0 0 8px; max-width: 80ch; }

@media (max-width: 600px) {
  .picks { display: flex; overflow-x: auto; scroll-snap-type: x mandatory; gap: 10px; padding-bottom: 6px; scrollbar-width: thin; }
  .pick { flex: 0 0 76%; scroll-snap-align: start; }
  .row { grid-template-columns: 60px minmax(0, 1fr); }
  .row__watch { grid-column: 2; justify-self: start; padding-top: 0; }
  .row--on .row__watch { display: none; }   /* the colored pill already says it on a phone */
  .nextup { grid-template-columns: 1fr; }
  .nextup__count { font-size: 32px; }
  .row__time .t { font-size: 22px; }
  .hdr__tally { text-align: left; }
  .controls__lbl { min-width: 0; width: 100%; }
}
@media (prefers-reduced-motion: no-preference) { .pick { transition: border-color 120ms ease; } }
</style>
<style id="logos">@@LOGO_CSS@@</style>
<script type="application/json" id="league-meta">@@LEAGUE_META@@</script>
<script type="application/json" id="service-meta">@@SERVICE_META@@</script>

<div class="wrap" id="app" data-built="@@BUILT_ISO@@">
  <header class="hdr">
    <div>
      <div class="hdr__eyebrow" id="eyebrow">Your lineup · the week ahead</div>
      <h1>Soccer Outlook</h1>
    </div>
    <div class="hdr__tally"><div class="big" id="tally-n">@@N_ON@@</div><div class="small" id="tally-txt">of @@N_ALL@@ upcoming matches tracked this week are on your services</div></div>
  </header>
  <div class="forecast" id="forecast"><p>Matches on HBO Max, Fox One, Paramount+, ESPN Unlimited, Apple TV, USA Network, Prime Video, Netflix and Disney+, from the moment you open this page through the week ahead.</p></div>
  <p class="fresh" id="fresh">Fixtures and broadcasters from ESPN as of @@BUILT_ET@@. Rebuilt early morning, midday and evening. Times shown in Eastern.</p>
  <div class="stale" id="stale" hidden></div>
  <div class="nextup" id="nextup" hidden>
    <div class="nextup__left"><div class="nextup__status" id="nextup-status">Kickoff in</div><div class="nextup__count" id="nextup-count">–</div></div>
    <div class="nextup__match" id="nextup-match"></div>
  </div>

  <section class="sec" aria-labelledby="picks-h">
    <div class="sec__h"><h2 id="picks-h">Worth planning around</h2><p id="picks-sub">On your services, today and tomorrow</p></div>
    <div class="picks" id="picks">@@PICKS@@</div>
  </section>

  <div class="controls mode-mine" id="controls">
    <div class="controls__row controls__bar">
      <div class="seg" role="group" aria-label="Which matches to show">
        <button type="button" id="btn-mine" aria-pressed="true">On my services</button>
        <button type="button" id="btn-all" aria-pressed="false">Everything</button>
      </div>
      <button type="button" class="fbtn" id="btn-filters" aria-expanded="false" aria-controls="drawer">Lineup &amp; filters<span class="fbtn__sum" id="filter-sum"></span><span class="fbtn__caret" aria-hidden="true">&#9662;</span></button>
      <span class="tiny"><button type="button" class="link" id="btn-reset">Reset</button></span>
    </div>
  </div>
  <div class="drawer" id="drawer" hidden>
    <div class="controls__row" id="have-pills"><span class="controls__lbl">You have</span>@@HAVE_PILLS@@</div>
    <p class="drawer__hint">Tap the services you have and every match is judged against them. The default is the page owner's lineup; your choice stays in this browser. A cable, YouTube TV, Fubo or Hulu Live package counts as the live-TV bundle.</p>
    <div class="controls__row" id="comp-pills"><span class="controls__lbl">Competitions</span>@@COMP_PILLS@@</div>
    <div class="controls__row drawer__foot"><button type="button" class="fbtn" id="btn-filters-close">Done</button></div>
  </div>

  <section class="sec" id="outlook" aria-labelledby="outlook-h" style="margin-top:18px">
    <div class="sec__h"><h2 id="outlook-h">The outlook</h2><p id="outlook-sub">Grouped by Eastern day until the page script runs</p></div>
    <div id="outlook-body">@@OUTLOOK@@</div>
  </section>

  <section class="sec" aria-labelledby="miss-h">
    <div class="sec__h"><h2 id="miss-h">Good ones you can't get</h2><p>Today and tomorrow, with where they actually are</p></div>
    <div class="misses" id="misses"></div>
  </section>

  <section class="sec" aria-labelledby="lineup-h">
    <div class="sec__h"><h2 id="lineup-h">Your lineup</h2><p>The services you have, counting every competition</p></div>
    <div class="lineup" id="lineup">@@LINEUP@@</div>
  </section>

  <section class="sec" id="tables" aria-labelledby="tables-h">
    <div class="sec__h"><h2 id="tables-h">Tables</h2><p>Standings for the leagues playing this week; teams with a match in the next three days are shaded</p></div>
    @@TABLES@@
  </section>

  <footer class="foot">
    <p>A colored pill means the broadcaster is inside one of the services you have selected; a grey pill is one you don't have; a dashed pill marks the league's usual home when ESPN has not listed the channel yet, which is normal more than a few days out. Fox One includes FOX, FS1, FS2, Big Ten Network and Fox Deportes but not Fox Soccer Plus. ESPN Unlimited includes every ESPN network, ESPN on ABC and ESPN+. TNT and TBS matches stream on HBO Max; CBS matches stream on Paramount+ Premium. Assignments can move on the day, so a glance at the app before kickoff is still worth it.</p>
    @@FAILED@@
    <p>Fixtures, scores, broadcasters and logos from ESPN's public scoreboard. Rights notes from Fox Sports, CBS Sports, ESPN and World Soccer Talk. Built by <a href="https://github.com/caparomula/soccer-outlook">a small open generator</a> on GitHub.</p>
  </footer>
</div>

<script>
(function () {
  'use strict';
  var DAY_START = 4;                 // a sports day runs 4 am to 4 am local time
  var LIVE_MS = 125 * 60000;
  var LS = { mode: 'ssg2-mode', comp: 'ssg2-comp-off' };
  var app = document.getElementById('app');
  var body = document.getElementById('outlook-body');
  var rows = Array.prototype.slice.call(document.querySelectorAll('li.row'));
  if (!rows.length) return;
  var controls = document.getElementById('controls');
  var META = {}; try { META = JSON.parse(document.getElementById('league-meta').textContent) || {}; } catch (e) {}
  // A hash such as #at-20261003-2130 pins "now" (viewer-local) so a perspective can be previewed.
  function nowMs() { var m = /^#at-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$/.exec(location.hash || ''); return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime() : Date.now(); }
  var picksEl = document.getElementById('picks'), missesEl = document.getElementById('misses');
  var SERVICES = { order: [], name: {}, owner: [] };
  try { SERVICES = JSON.parse(document.getElementById('service-meta').textContent) || SERVICES; } catch (e) {}
  var SERVICE_NAMES = SERVICES.name;
  function rankOf(id) { var i = SERVICES.order.indexOf(id); return i < 0 ? 99 : i; }
  var SHORT = { cable: 'cable', ota: 'antenna', free: 'free app' };   // buckets where the channel leads
  function escHtml(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  var ORDER = ['live', 'morning', 'afternoon', 'evening', 'tonight', 'tomorrow', 'week', 'earlier', 'yesterday'];
  var TITLES = { live: 'Live now', morning: 'This morning', afternoon: 'This afternoon', evening: 'This evening', tonight: 'Tonight', tomorrow: 'Tomorrow', week: 'Later this week', earlier: 'Earlier today', yesterday: 'Yesterday' };
  var FOLDED = { earlier: true, yesterday: true };

  rows.forEach(function (r) {
    r._k = Date.parse(r.getAttribute('data-utc'));
    r._tv = r.getAttribute('data-tv') === '1';
    r._svc = r.getAttribute('data-svc');
    r._lg = r.getAttribute('data-lg');
    r._score = parseInt(r.getAttribute('data-score'), 10) || 0;
    r._state = r.getAttribute('data-state');
    try { r._o = JSON.parse(r.getAttribute('data-o') || '[]'); } catch (e) { r._o = []; }
    try { r._r = r.hasAttribute('data-r') ? JSON.parse(r.getAttribute('data-r')) : null; } catch (e) { r._r = null; }
  });

  function read(key) { try { var v = localStorage.getItem(key); return v ? JSON.parse(v) : null; } catch (e) { return null; } }
  function write(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) {} }

  // ---- lineup and filters ----------------------------------------------------------------------
  var mode = read(LS.mode) === 'all' ? 'all' : 'mine';
  var drawerOpen = read('ssg2-drawer') === true;
  var drawer = document.getElementById('drawer'), btnFilters = document.getElementById('btn-filters');
  var storedHave = read('ssg3-have');
  var HAVE = {};
  (Array.isArray(storedHave) ? storedHave : SERVICES.owner).forEach(function (k) { HAVE[k] = true; });
  var compOff = {};
  var storedComp = read(LS.comp);
  if (storedComp) { storedComp.forEach(function (k) { compOff[k] = true; }); }
  else { drawer.querySelectorAll('[data-kind="comp"][data-default-off="1"]').forEach(function (b) { compOff[b.getAttribute('data-key')] = true; }); }

  function firstHave(via) { var hits = via.filter(function (x) { return HAVE[x]; }); hits.sort(function (a, b) { return rankOf(a) - rankOf(b); }); return hits[0] || ''; }
  function chipFor(r) {
    if (SHORT[r._svc]) return '<span class="chip svc-' + r._svc + '"><i class="dot"></i>' + escHtml(r._outlet) + '<span class="chip__via">' + SHORT[r._svc] + '</span></span>';
    if (r._svc !== 'none') {
      var name = SERVICE_NAMES[r._svc] || r._svc;
      if (r._basis === 'rule') return '<span class="chip chip--rule svc-' + r._svc + '"><i class="dot"></i>' + escHtml(name) + '<span class="chip__via">usually</span></span>';
      var via = r._outlet && r._outlet !== name ? '<span class="chip__via">' + escHtml(r._outlet) + '</span>' : '';
      return '<span class="chip svc-' + r._svc + '"><i class="dot"></i>' + escHtml(name) + via + '</span>';
    }
    return r._o.length ? '<span class="chip chip--no">Not in your lineup</span>' : '<span class="chip chip--no chip--unk">Not listed yet</span>';
  }
  // Decide, for this viewer's lineup, which service carries each match, and restyle the row to match.
  function evaluateRow(r) {
    var best = null;
    r._o.forEach(function (o) {
      o.v.forEach(function (sid) {
        if (!HAVE[sid]) return;
        var key = rankOf(sid) * 4 + (o.l === SERVICE_NAMES[sid] ? 0 : 2) + (o.e ? 1 : 0);
        if (!best || key < best.key) best = { key: key, sid: sid, label: o.l };
      });
    });
    if (best) { r._svc = best.sid; r._basis = 'listed'; r._outlet = best.label; }
    else if (r._r && firstHave(r._r.v)) { r._svc = firstHave(r._r.v); r._basis = 'rule'; r._outlet = r._r.l; }
    else { r._svc = 'none'; r._basis = 'none'; r._outlet = ''; }
    r.classList.toggle('row--on', r._svc !== 'none'); r.classList.toggle('row--off', r._svc === 'none'); r.classList.toggle('row--rule', r._basis === 'rule');
    r.setAttribute('data-svc', r._svc); r.setAttribute('data-basis', r._basis); r.setAttribute('data-outlet', r._outlet);
    r.querySelectorAll('.pill[data-i]').forEach(function (p) {
      var o = r._o[+p.getAttribute('data-i')]; if (!o) return;
      var sid = firstHave(o.v);
      p.className = 'pill' + (sid ? ' pill--mine svc-' + sid : (o.f ? ' pill--free' : ''));
    });
    var rp = r.querySelector('.pill[data-rule]');
    if (rp && r._r) { var rs = firstHave(r._r.v); rp.className = 'pill pill--rule' + (rs ? ' svc-' + rs : ' pill--rule-off'); }
    var w = r.querySelector('.row__watch'); if (w) w.innerHTML = chipFor(r);
  }
  function evaluateAll() { rows.forEach(evaluateRow); }

  function filterSummary() {
    var n = Object.keys(HAVE).length, c = Object.keys(compOff).length, parts = [];
    parts.push(Array.isArray(storedHave) ? n + (n === 1 ? ' service' : ' services') : "owner's lineup");
    if (c) parts.push(c + (c === 1 ? ' competition hidden' : ' competitions hidden'));
    return parts.join(', ');
  }
  function applyFilterUI() {
    controls.classList.toggle('mode-mine', mode === 'mine');
    drawer.hidden = !drawerOpen; btnFilters.setAttribute('aria-expanded', String(drawerOpen));
    document.getElementById('filter-sum').textContent = filterSummary();
    document.getElementById('btn-mine').setAttribute('aria-pressed', String(mode === 'mine'));
    document.getElementById('btn-all').setAttribute('aria-pressed', String(mode === 'all'));
    drawer.querySelectorAll('.fpill').forEach(function (b) {
      var k = b.getAttribute('data-key');
      var on = b.getAttribute('data-kind') === 'have' ? !!HAVE[k] : !compOff[k];
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  function passes(r) {
    if (mode === 'mine' && r._svc === 'none') return false;
    if (compOff[r._lg]) return false;
    return true;
  }
  app.addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (!b || !(b.closest('#controls') || b.closest('#drawer'))) return;
    if (b.id === 'btn-filters' || b.id === 'btn-filters-close') {
      drawerOpen = b.id === 'btn-filters' ? !drawerOpen : false; write('ssg2-drawer', drawerOpen); applyFilterUI();
      if (!drawerOpen && b.id === 'btn-filters-close') controls.scrollIntoView({ block: 'start' });
      return;
    }
    if (b.id === 'btn-mine' || b.id === 'btn-all') { mode = b.id === 'btn-all' ? 'all' : 'mine'; write(LS.mode, mode); }
    else if (b.id === 'btn-reset') {
      mode = 'mine'; HAVE = {}; SERVICES.owner.forEach(function (k) { HAVE[k] = true; }); storedHave = null;
      compOff = {}; drawer.querySelectorAll('[data-kind="comp"][data-default-off="1"]').forEach(function (x) { compOff[x.getAttribute('data-key')] = true; });
      write(LS.mode, mode); try { localStorage.removeItem(LS.comp); localStorage.removeItem('ssg3-have'); } catch (e) {}
      evaluateAll();
    }
    else if (b.getAttribute('data-kind') === 'have') {
      var k = b.getAttribute('data-key'); if (HAVE[k]) delete HAVE[k]; else HAVE[k] = true;
      storedHave = Object.keys(HAVE); write('ssg3-have', storedHave); evaluateAll();
    }
    else if (b.getAttribute('data-kind') === 'comp') { var c = b.getAttribute('data-key'); if (compOff[c]) delete compOff[c]; else compOff[c] = true; write(LS.comp, Object.keys(compOff)); }
    else return;
    applyFilterUI(); render(true);
  });

  // ---- time helpers --------------------------------------------------------------------------
  var tz = null; try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) {}
  var showET = tz && tz !== 'America/New_York';
  var fmtTime = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  var fmtET = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' });
  var fmtDay = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
  var fmtShortDay = new Intl.DateTimeFormat(undefined, { weekday: 'short' });
  var fmtLongDay = new Intl.DateTimeFormat(undefined, { weekday: 'long' });
  function splitTime(d) {
    var parts = fmtTime.formatToParts(d), h = '', m = '', ap = '';
    parts.forEach(function (p) { if (p.type === 'hour') h = p.value; else if (p.type === 'minute') m = p.value; else if (p.type === 'dayPeriod') ap = p.value.toLowerCase(); });
    return { t: m ? h + ':' + m : h, ap: ap };
  }
  function sportsDayStart(d) { var s = new Date(d); s.setHours(DAY_START, 0, 0, 0); if (d < s) s.setDate(s.getDate() - 1); return s; }
  function dayIndex(k, now) { return Math.round((sportsDayStart(new Date(k)) - sportsDayStart(new Date(now))) / 86400000); }
  function bucketOf(r, now) {
    var idx = dayIndex(r._k, now);
    if (idx < 0) return idx === -1 ? 'yesterday' : null;
    if (idx === 0) {
      if (r._state === 'post') return 'earlier';
      if (r._state === 'in' && now < r._k + LIVE_MS + 30 * 60000) return 'live';
      if (r._tv && now >= r._k && now < r._k + LIVE_MS) return 'live';
      if (r._tv && now >= r._k + LIVE_MS) return 'earlier';
      if (!r._tv) return 'tonight';
      var h = new Date(r._k).getHours(); if (h < DAY_START) h += 24;
      return h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 20 ? 'evening' : 'tonight';
    }
    if (idx === 1) return 'tomorrow';
    if (idx <= 8) return 'week';
    return null;
  }
  function nowBucketName(now) {
    var h = new Date(now).getHours(); if (h < DAY_START) h += 24;
    return h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 20 ? 'evening' : 'tonight';
  }

  // Write local kickoff times into the rows once (static markup carries Eastern time).
  rows.forEach(function (r) {
    if (!r._tv) return;
    var st = splitTime(new Date(r._k));
    var t = r.querySelector('[data-t]'), ap = r.querySelector('[data-ap]'), et = r.querySelector('.row__et');
    if (t) t.textContent = st.t; if (ap) ap.textContent = st.ap;
    if (showET && et) { et.textContent = fmtET.format(new Date(r._k)) + ' ET'; et.hidden = false; }
  });
  var tzName = tz || 'local time';
  try { tzName = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(new Date()).filter(function (p) { return p.type === 'timeZoneName'; })[0].value; } catch (e) {}
  document.getElementById('fresh').textContent = document.getElementById('fresh').textContent.replace('Times shown in Eastern.', showET ? 'Times shown in ' + tzName + ', with Eastern underneath.' : 'Times shown in Eastern.');
  document.getElementById('outlook-sub').textContent = 'From where you are right now; refreshes itself every minute';

  // ---- rendering -----------------------------------------------------------------------------
  var lastSig = '';
  function render(force) {
    var now = nowMs();
    var groups = {}; ORDER.forEach(function (b) { groups[b] = []; });
    var sig = mode + '|' + Object.keys(HAVE).join(',') + '|' + Object.keys(compOff).join(',') + '|';
    var all = {}; ORDER.forEach(function (b) { all[b] = []; });
    rows.forEach(function (r) {
      var b = bucketOf(r, now);
      r._b = b; r.hidden = !(b && passes(r));
      if (b) all[b].push(r);
      if (b && !r.hidden) groups[b].push(r);
      sig += (b || '-')[0];
    });
    ORDER.forEach(function (b) { all[b].sort(function (a, c) { return a._k - c._k || c._score - a._score; }); });
    rows.forEach(function (r) { var l = r.querySelector('.row__live'); if (l) l.hidden = r._b !== 'live'; });
    if (!force && sig === lastSig) { renderLede(groups, all, now); return; }
    lastSig = sig;

    var frag = document.createDocumentFragment();
    var current = nowBucketName(now), anyUpcoming = false;
    ORDER.forEach(function (b) {
      var list = groups[b];
      if (!list.length) return;
      list.sort(function (a, c) { return a._k - c._k || c._score - a._score; });
      var sec, host;
      var h = document.createElement('h3'); h.className = 'bucket__h';
      var title = TITLES[b];
      var when = '';
      if (b === 'tomorrow') when = fmtDay.format(new Date(list[0]._k));
      if (b === 'live') title = 'Live now';
      h.innerHTML = '<span></span><span class="when"></span><span class="bucket__count"></span>';
      h.firstChild.textContent = title;
      h.children[1].textContent = when;
      h.children[2].textContent = list.length + (list.length === 1 ? ' match' : ' matches');
      if (FOLDED[b]) {
        sec = document.createElement('details'); sec.className = 'fold bucket';
        var sum = document.createElement('summary'); sum.appendChild(h);
        var car = document.createElement('span'); car.className = 'caret'; car.innerHTML = ' <span class="c">show &#9662;</span><span class="o">hide &#9652;</span>'; h.children[2].appendChild(car);
        sec.appendChild(sum); host = sec;
      } else {
        sec = document.createElement('section'); sec.className = 'bucket' + (b === 'live' ? ' bucket--live' : (b === current ? ' bucket--now' : ''));
        sec.appendChild(h); host = sec;
        anyUpcoming = true;
      }
      if (b === 'week') {
        var byDay = {};
        list.forEach(function (r) { var d = sportsDayStart(new Date(r._k)).toDateString(); (byDay[d] = byDay[d] || []).push(r); });
        Object.keys(byDay).forEach(function (d) {
          var dh = document.createElement('div'); dh.className = 'dayhead'; dh.textContent = fmtDay.format(new Date(byDay[d][0]._k)); host.appendChild(dh);
          var ol = document.createElement('ol'); ol.className = 'rows'; byDay[d].forEach(function (r) { ol.appendChild(r); }); host.appendChild(ol);
        });
      } else {
        var ol2 = document.createElement('ol'); ol2.className = 'rows'; list.forEach(function (r) { ol2.appendChild(r); }); host.appendChild(ol2);
      }
      frag.appendChild(sec);
    });
    if (!anyUpcoming) {
      var e = document.createElement('p'); e.className = 'empty';
      e.textContent = mode === 'mine' ? 'Nothing left on your services in this window with the current filters. Try "Everything" or turn a competition back on.' : 'Nothing left in this window with the current filters.';
      frag.appendChild(e);
    }
    // Rows not placed (outside the window) are parked out of sight.
    var park = document.getElementById('park') || (function () { var p = document.createElement('div'); p.id = 'park'; p.hidden = true; document.body.appendChild(p); return p; })();
    rows.forEach(function (r) { if (!r._b) park.appendChild(r); });
    body.innerHTML = ''; body.appendChild(frag);
    renderPicks(groups, now); renderMisses(all, now); renderLineup(all, now); renderLede(groups, all, now); renderNextup(groups, now);
  }

  // ---- next up: the Pit Dash countdown ---------------------------------------------------------
  var nextupEl = document.getElementById('nextup'), nextRow = null, nextLive = false;
  function fmtCount(ms) {
    var s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (h >= 1) return h + 'h ' + (m < 10 ? '0' : '') + m + 'm';
    return m + ':' + (sec < 10 ? '0' : '') + sec;
  }
  function renderNextup(groups, now) {
    var live = groups.live.filter(onSvc).sort(byTime)[0];
    var next = [].concat(groups.morning, groups.afternoon, groups.evening, groups.tonight, groups.tomorrow, groups.week).filter(function (r) { return onSvc(r) && r._k > now; }).sort(byTime)[0];
    nextRow = live || next || null; nextLive = !!live;
    nextupEl.hidden = !nextRow;
    if (!nextRow) return;
    nextupEl.classList.toggle('nextup--live', nextLive);
    var m = document.getElementById('nextup-match'); m.innerHTML = '';
    m.appendChild(logoClone(nextRow, 0, 'logo'));
    var names = document.createElement('span'); names.className = 'nextup__names'; names.textContent = matchName(nextRow); m.appendChild(names);
    m.appendChild(logoClone(nextRow, 1, 'logo'));
    var meta = document.createElement('span'); meta.className = 'nextup__meta'; meta.textContent = nextRow.getAttribute('data-comp') + ' \u00b7 ' + (nextRow._tv ? proseTime(nextRow) + dayTag(nextRow, now) : 'time TBD'); m.appendChild(meta);
    var svc = document.createElement('span'); svc.className = 'nextup__svc svc-' + nextRow._svc; svc.innerHTML = '<i class="dot"></i>';
    svc.appendChild(document.createTextNode(proseWhere(nextRow).replace(/^on /, ''))); m.appendChild(svc);
    tickNextup();
  }
  function tickNextup() {
    if (!nextRow || nextupEl.hidden) return;
    var now = nowMs(), diff = nextRow._k - now, status, count;
    if (nextLive || diff <= 0) {
      var mins = Math.floor(-diff / 60000);
      status = 'Live now'; count = mins < 1 ? 'kicked off' : mins + ' min in';
    } else if (diff < 15 * 60000) { status = 'Starting soon'; count = fmtCount(diff); }
    else if (diff < 24 * 3600000) { status = 'Kickoff in'; count = fmtCount(diff); }
    else { status = 'Next up'; count = fmtShortDay.format(new Date(nextRow._k)) + ' ' + proseTime(nextRow); }
    document.getElementById('nextup-status').textContent = status;
    document.getElementById('nextup-count').textContent = count;
    // Per-row countdowns for today's upcoming matches.
    rows.forEach(function (r) {
      var el = r.querySelector('.row__until'); if (!el) return;
      var d = r._k - now;
      var show = r._tv && r._state === 'pre' && d > 0 && d < 12 * 3600000 && r._b && r._b !== 'live';
      el.hidden = !show; if (show) el.textContent = 'in ' + fmtCount(d);
    });
  }
  setInterval(tickNextup, 1000);

  // ---- details panels and table links ---------------------------------------------------------
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('button.more');
    if (btn) {
      var panel = btn.closest('.row__body').querySelector('.row__detail'); if (!panel) return;
      panel.hidden = !panel.hidden; btn.setAttribute('aria-expanded', String(!panel.hidden)); btn.textContent = panel.hidden ? 'Details' : 'Hide details';
      return;
    }
    var tl = ev.target.closest('a.detail__table');
    if (tl) { var d = document.querySelector('.tables__item[data-lg="' + tl.getAttribute('data-lg') + '"]'); if (d) { d.open = true; } }
  });

  function upcoming(groups) { return [].concat(groups.live, groups.morning, groups.afternoon, groups.evening, groups.tonight, groups.tomorrow); }
  function logoClone(r, i, cls) { var l = r.querySelectorAll('.logo')[i]; var c = l ? l.cloneNode(true) : document.createElement('i'); c.className = cls + (c.className.indexOf('logo--txt') > -1 ? ' logo--txt' : '') + (l ? ' ' + Array.prototype.filter.call(l.classList, function (x) { return x.indexOf('l-') === 0; }).join(' ') : ''); return c; }
  function timeLabel(r) { if (!r._tv) return 'TBD'; var st = splitTime(new Date(r._k)); return st.t + ' ' + st.ap; }
  function dayTag(r, now) { var idx = dayIndex(r._k, now); return idx === 0 ? '' : idx === 1 ? ' tomorrow' : ' ' + fmtShortDay.format(new Date(r._k)); }

  function renderPicks(groups, now) {
    var pool = upcoming(groups).filter(function (r) { return r._svc !== 'none'; });
    function pickScore(r) { return r._score + (r._b === 'live' ? 20 : 0); }
    var chosen = pool.slice().sort(function (a, c) { return pickScore(c) - pickScore(a) || a._k - c._k; }).slice(0, 4);
    if (chosen.length < 2) chosen = chosen.concat(groups.week.filter(function (r) { return r._svc !== 'none' && chosen.indexOf(r) < 0; }).sort(function (a, c) { return c._score - a._score || a._k - c._k; }).slice(0, 4 - chosen.length));
    chosen.sort(function (a, c) { return a._k - c._k; });   // chosen by stature, shown in kickoff order
    picksEl.innerHTML = '';
    document.getElementById('picks-sub').textContent = chosen.some(function (r) { return r._b === 'week'; }) ? 'On your services, looking ahead' : 'On your services, today and tomorrow';
    chosen.forEach(function (r) {
      var a = document.createElement('a'); a.className = 'pick svc-' + r._svc; a.href = '#outlook';
      var when = document.createElement('div'); when.className = 'pick__when';
      var st = r._tv ? splitTime(new Date(r._k)) : { t: 'TBD', ap: '' };
      when.innerHTML = '<span class="t"></span><span class="ap"></span>';
      when.firstChild.textContent = st.t; when.lastChild.textContent = (r._b === 'live' ? 'live now' : st.ap + dayTag(r, now));
      var teams = document.createElement('div'); teams.className = 'pick__teams';
      teams.appendChild(logoClone(r, 0, 'logo logo--lg')); var vs = document.createElement('span'); vs.className = 'pick__vs'; vs.textContent = 'v'; teams.appendChild(vs); teams.appendChild(logoClone(r, 1, 'logo logo--lg'));
      var names = document.createElement('div'); names.className = 'pick__names'; names.textContent = r.getAttribute('data-home') + ' v ' + r.getAttribute('data-away');
      var subs = Array.prototype.map.call(r.querySelectorAll('.team'), function (t) { var x = t.querySelector('.team__sub'); return x ? x.textContent.trim() : ''; });
      var sub = null;
      if (subs.some(Boolean)) { sub = document.createElement('div'); sub.className = 'pick__sub'; sub.textContent = (subs[0] || '\u2013') + ' v ' + (subs[1] || '\u2013'); }
      var comp = document.createElement('div'); comp.className = 'pick__comp'; comp.textContent = r.getAttribute('data-comp');
      var colors = document.createElement('div'); colors.className = 'pick__colors';
      [r.getAttribute('data-hc'), r.getAttribute('data-ac')].forEach(function (c) { var i = document.createElement('i'); i.style.background = /^[0-9a-f]{6}$/.test(c || '') ? '#' + c : 'var(--line-strong)'; colors.appendChild(i); });
      var svc = document.createElement('div'); svc.className = 'pick__svc'; svc.innerHTML = '<i class="dot"></i>';
      var outlet = r.getAttribute('data-outlet'), sname = SERVICE_NAMES[r._svc] || r._svc;
      svc.appendChild(document.createTextNode(SHORT[r._svc] ? outlet + ' · ' + SHORT[r._svc] : sname + (outlet && outlet !== sname ? ' · ' + outlet : '') + (r.getAttribute('data-basis') === 'rule' ? ' (usually)' : '')));
      a.appendChild(when); a.appendChild(teams); a.appendChild(names); if (sub) a.appendChild(sub); a.appendChild(comp); a.appendChild(svc); a.appendChild(colors);
      picksEl.appendChild(a);
    });
  }

  function renderMisses(groups, now) {
    var pool = upcoming(groups).filter(function (r) { return r._svc === 'none' && r._score >= 85 && !compOff[r._lg]; }).sort(function (a, c) { return c._score - a._score || a._k - c._k; }).slice(0, 4);
    missesEl.innerHTML = '';
    pool.forEach(function (r) {
      var d = document.createElement('div'); d.className = 'miss';
      var teams = document.createElement('div'); teams.className = 'miss__teams';
      teams.appendChild(logoClone(r, 0, 'logo')); var vs = document.createElement('span'); vs.className = 'miss__vs'; vs.textContent = 'v'; teams.appendChild(vs); teams.appendChild(logoClone(r, 1, 'logo'));
      var bodyEl = document.createElement('div');
      var names = document.createElement('div'); names.className = 'miss__names'; names.textContent = r.getAttribute('data-home') + ' v ' + r.getAttribute('data-away') + ' ';
      var tm = document.createElement('span'); tm.className = 'miss__time'; tm.textContent = timeLabel(r) + dayTag(r, now); names.appendChild(tm);
      var where = document.createElement('div'); where.className = 'miss__where';
      var pills = r.querySelector('.pills'); if (pills) where.innerHTML = pills.innerHTML;
      bodyEl.appendChild(names); bodyEl.appendChild(where);
      d.appendChild(teams); d.appendChild(bodyEl); missesEl.appendChild(d);
    });
  }

  function renderLineup(groups, now) {
    var today = [].concat(groups.live, groups.morning, groups.afternoon, groups.evening, groups.tonight);
    var week = upcoming(groups).concat(groups.week);
    var host = document.getElementById('lineup'); host.innerHTML = '';
    var ids = SERVICES.order.filter(function (id) { return HAVE[id]; });
    if (!ids.length) { var e = document.createElement('p'); e.className = 'empty'; e.textContent = 'No services selected. Open "Lineup & filters" and tap the ones you have.'; host.appendChild(e); return; }
    ids.forEach(function (k) {
      var t = today.filter(function (r) { return r._svc === k; }), w = week.filter(function (r) { return r._svc === k; });
      var card = document.createElement('div'); card.className = 'svc svc-' + k + (w.length ? '' : ' svc--quiet'); card.setAttribute('data-svc', k);
      var head = document.createElement('div'); head.className = 'svc__head';
      head.innerHTML = '<i class="dot"></i><span class="svc__name"></span><span class="svc__count"></span>';
      head.children[1].textContent = SERVICE_NAMES[k] || k;
      head.children[2].textContent = t.length + ' today · ' + w.length + ' this week';
      var desc = document.createElement('p'); desc.className = 'svc__desc';
      var next = w.slice().sort(byTime)[0];
      desc.textContent = next ? 'Next: ' + matchName(next) + ', ' + timeLabel(next) + dayTag(next, now) + (next._outlet && next._outlet !== (SERVICE_NAMES[k] || '') ? ' on ' + next._outlet : '') + (next._basis === 'rule' ? ' (usual home; channel not posted yet)' : '') + '.' : 'Nothing listed in the next week.';
      card.appendChild(head); card.appendChild(desc); host.appendChild(card);
    });
  }

  // ---- the forecast ----------------------------------------------------------------------------
  function onSvc(r) { return r._svc !== 'none'; }
  function byScore(a, c) { return c._score - a._score || a._k - c._k; }
  function byTime(a, c) { return a._k - c._k; }
  function cap(t) { return t.charAt(0).toUpperCase() + t.slice(1); }
  function lgName(lg, the) { var m = META[lg] || { name: lg, the: false }; return (the && m.the ? 'the ' : '') + m.name; }
  function joinList(items, word) { word = word || 'and'; if (items.length <= 1) return items.join(''); if (items.length === 2) return items[0] + ' ' + word + ' ' + items[1]; return items.slice(0, -1).join(', ') + ' ' + word + ' ' + items[items.length - 1]; }
  function matchName(r) { return r.getAttribute('data-home') + ' v ' + r.getAttribute('data-away'); }
  function proseTime(r) {
    if (!r._tv) return 'a time to be set';
    var st = splitTime(new Date(r._k));
    if (st.t === '12:00' && st.ap === 'pm') return 'noon';
    if (st.t === '12:00' && st.ap === 'am') return 'midnight';
    return st.t.replace(/:00$/, '') + ' ' + st.ap;
  }
  function proseWhere(r) {
    var sname = SERVICE_NAMES[r._svc] || r._svc, o = r.getAttribute('data-outlet'), t = 'on ' + sname;
    if (SHORT[r._svc]) return 'on ' + o + ' (' + SHORT[r._svc] + ')';
    if (r.getAttribute('data-basis') === 'rule') return t + ' (usually ' + o + ')';
    if (o && o !== sname) t += ' (' + o + ')';
    return t;
  }
  function slateItem(r) { return matchName(r) + ' at ' + proseTime(r) + ' ' + proseWhere(r); }
  function liveItem(r) { return matchName(r) + ' ' + proseWhere(r); }

  function composeForecast(groups, all, now) {
    var todayAll = [].concat(all.earlier, all.live, all.morning, all.afternoon, all.evening, all.tonight);
    var later = [].concat(all.tomorrow, all.week);
    var lgToday = {}, lgTodayOn = {}, catToday = {};
    todayAll.forEach(function (r) {
      lgToday[r._lg] = (lgToday[r._lg] || 0) + 1;
      if (onSvc(r)) lgTodayOn[r._lg] = (lgTodayOn[r._lg] || 0) + 1;
      var c = (META[r._lg] || {}).cat || 'other'; catToday[c] = (catToday[c] || 0) + 1;
    });
    function firstLater(lg) { for (var i = 0; i < later.length; i++) if (later[i]._lg === lg) return later[i]; return null; }
    function names(list, the) { return list.map(function (lg) { return lgName(lg, the); }); }
    var d = new Date(now), weekday = fmtLongDay.format(d), weekend = d.getDay() === 0 || d.getDay() === 6;
    var BIG = ['eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1'];
    var bigOn = BIG.filter(function (lg) { return lgToday[lg]; });
    var darkList = BIG.concat(['usa.1']).filter(function (lg) { return !lgToday[lg] && firstLater(lg); });
    var uclOn = ['uefa.champions', 'uefa.europa', 'uefa.europa.conf'].filter(function (lg) { return lgToday[lg]; });
    var label, s1;
    if (uclOn.length && (catToday.ucl || 0) >= 4) {
      label = 'European night';
      s1 = 'A European ' + weekday + ': ' + joinList(names(uclOn, true)) + (uclOn.length > 1 ? ' are' : ' is') + ' on' + (bigOn.length ? '' : ', and the domestic leagues wait for the weekend') + '.';
    } else if (!bigOn.length && (catToday.nat || 0) >= 4) {
      label = 'International break';
      var natParts = [];
      if (lgToday['uefa.nations']) natParts.push(lgToday['uefa.nations'] + ' Nations League matches');
      var fr = (lgToday['fifa.friendly'] || 0) + (lgToday['fifa.friendly.w'] || 0);
      if (fr) natParts.push(fr + (fr === 1 ? ' friendly' : ' friendlies'));
      if (lgToday['concacaf.nations.league']) natParts.push(lgToday['concacaf.nations.league'] + ' in the Concacaf Nations League');
      s1 = 'The club leagues are dark for the international break: the day belongs to the national teams, with ' + joinList(natParts) +
        (darkList.length ? ', and no ' + joinList(names(darkList, false), 'or') : '') + '.';
    } else if (bigOn.length) {
      label = weekend ? 'Club weekend' : 'Midweek club football';
      var waits = BIG.filter(function (lg) { return !lgToday[lg] && firstLater(lg); });
      s1 = 'A ' + (weekend ? 'full club ' : 'midweek ') + weekday + ': ' + joinList(names(bigOn, true)) + (bigOn.length > 1 ? ' are' : ' is') + ' on' +
        (waits.length ? ', while ' + joinList(names(waits, true)) + (waits.length > 1 ? ' wait' : ' waits') + ' until ' + fmtLongDay.format(new Date(firstLater(waits[0])._k)) : '') + '.';
    } else {
      label = todayAll.length ? 'Quiet day' : 'Nothing today';
      s1 = todayAll.length ? 'A quiet ' + weekday + ', with ' + todayAll.length + ' matches tracked' + (darkList.length ? ' and no ' + joinList(names(darkList, false), 'or') : '') + '.' : 'No matches are tracked for ' + weekday + '.';
    }
    // Competitions filling in around the headline: the ones on your services first, then by stature, skipping hidden ones.
    var fill = Object.keys(lgToday).filter(function (lg) { var c = (META[lg] || {}).cat || 'other'; return c !== 'big' && c !== 'nat' && c !== 'ucl'; });
    function fillRank(lg) { var m = META[lg] || {}; return (lgTodayOn[lg] ? 100 : 0) - (compOff[lg] ? 1000 : 0) - 10 * (m.tier || 3) + Math.min(lgToday[lg], 9); }
    fill.sort(function (a, b) { return fillRank(b) - fillRank(a); });
    fill = fill.filter(function (lg) { return !compOff[lg]; }).slice(0, 3);
    if (fill.length) s1 += ' ' + cap(joinList(names(fill, true))) + (label === 'International break' ? (fill.length > 1 ? ' fill in.' : ' fills in.') : (fill.length > 1 ? ' are on too.' : ' is on too.'));

    var live = groups.live.filter(onSvc).sort(byScore);
    var ahead = [].concat(groups.morning, groups.afternoon, groups.evening, groups.tonight).filter(function (r) { return onSvc(r) && r._state !== 'post'; }).sort(byScore).slice(0, 3).sort(byTime);
    var current = nowBucketName(now), lead, s2 = '';
    if (live.length) {
      lead = 'Live now';
      var liveItems = live.slice(0, 2).map(liveItem);
      if (live.length > 2) liveItems.push((live.length - 2) + ' more');
      s2 = joinList(liveItems) + '.';
      if (ahead.length) s2 += ' Still to come: ' + ahead.map(slateItem).join(', then ') + '.';
    } else {
      lead = current === 'tonight' ? 'Tonight' : current === 'evening' ? 'Still to come' : 'Your best slate';
      s2 = ahead.length ? ahead.map(slateItem).join(', then ') + '.' : (mode === 'mine' ? 'Nothing more on your services today with the current filters.' : 'Nothing more on your services today.');
    }

    var tm = groups.tomorrow.filter(onSvc).sort(byScore).slice(0, 2).sort(byTime);
    var s3 = tm.length ? 'Tomorrow brings ' + joinList(tm.map(slateItem)) + '.' : 'Tomorrow is quiet on your services.';

    var returns = {}, order = [];
    ['eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'usa.1', 'uefa.champions', 'uefa.europa'].forEach(function (lg) {
      if (lgToday[lg] || all.tomorrow.some(function (r) { return r._lg === lg; })) return;
      var r = null; for (var i = 0; i < all.week.length; i++) if (all.week[i]._lg === lg) { r = all.week[i]; break; }
      if (!r) return;
      var day = fmtLongDay.format(new Date(r._k));
      if (!returns[day]) { returns[day] = []; order.push({ day: day, k: r._k }); }
      returns[day].push(lgName(lg, true));
    });
    order.sort(function (a, b) { return a.k - b.k; });
    var s4 = '';
    if (order.length) {
      var first = order[0], rest = order.slice(1);
      s4 = 'Later this week ' + joinList(returns[first.day]) + (returns[first.day].length === 1 ? ' returns on ' : ' return on ') + first.day;
      rest.forEach(function (o) { s4 += '; ' + joinList(returns[o.day]) + ' on ' + o.day; });
      s4 += '.';
    }
    var pick = groups.week.filter(onSvc).sort(byScore)[0];
    if (pick && pick._score >= 125) s4 += (s4 ? ' ' : '') + 'The pick of the week ahead is ' + matchName(pick) + ', ' + fmtLongDay.format(new Date(pick._k)) + ' at ' + proseTime(pick) + ' ' + proseWhere(pick) + '.';
    return { label: label, s1: s1, lead: lead, s2: s2, s3: s3, s4: s4 };
  }

  function renderLede(groups, all, now) {
    var f = composeForecast(groups, all, now);
    var box = document.getElementById('forecast'); box.innerHTML = '';
    var p1 = document.createElement('p'); p1.textContent = f.s1; box.appendChild(p1);
    var p2 = document.createElement('p'); var b = document.createElement('b'); b.textContent = f.lead + ': '; p2.appendChild(b); p2.appendChild(document.createTextNode(f.s2)); box.appendChild(p2);
    var p3 = document.createElement('p'); p3.textContent = f.s3 + (f.s4 ? ' ' + f.s4 : ''); box.appendChild(p3);
    var d = new Date(now);
    document.getElementById('eyebrow').textContent = fmtDay.format(d) + ' · ' + f.label;
    var up = upcoming(groups).concat(groups.week), on = up.filter(onSvc);
    document.getElementById('tally-n').textContent = on.length;
    document.getElementById('tally-txt').textContent = mode === 'mine' ? 'matches on your services through the week, with the current filters' : 'of ' + up.length + ' matches shown through the week are on your services';
  }

  // ---- freshness -----------------------------------------------------------------------------
  function checkStale() {
    var built = Date.parse(app.getAttribute('data-built'));
    var age = (Date.now() - built) / 3600000;
    var el = document.getElementById('stale');
    if (age > 30) { el.textContent = 'This page was last rebuilt ' + Math.round(age) + ' hours ago; the daily refresh may have failed, so fixtures and channels could have moved.'; el.hidden = false; }
    else el.hidden = true;
  }

  evaluateAll();
  applyFilterUI();
  render(true);
  checkStale();
  setInterval(function () { render(false); checkStale(); }, 60000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { render(false); checkStale(); } });
})();
</script>
'''


# ----------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="site/index.html")
    ap.add_argument("--embed-images", action="store_true", help="embed images as data URIs instead of linking ESPN's server")
    ap.add_argument("--logos", default="logos.json", help="image cache for --embed-images, read and updated")
    ap.add_argument("--fragment", action="store_true", help="write the page body only, for hosts that add the document wrapper")
    ap.add_argument("--days-ahead", type=int, default=9)
    ap.add_argument("--days-back", type=int, default=1)
    ap.add_argument("--date", help="treat this Eastern date as today (testing)")
    ap.add_argument("--no-logos", action="store_true", help="no team or league images at all")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    built_at = datetime.now(timezone.utc)
    today = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else built_at.astimezone(ET).date()
    days = [today + timedelta(days=i) for i in range(-args.days_back, args.days_ahead + 1)]

    merged, league_logos, failed = fetch_scoreboards(days, args.workers)
    LEAGUE_LOGOS.update(league_logos)
    STANDINGS.update(fetch_standings(list(LEAGUES), args.workers))
    matches = []
    for lg, events in merged.items():
        for ev in events.values():
            try:
                m = interpret(lg, ev)
            except (KeyError, ValueError, TypeError) as e:
                print(f"skip {lg} {ev.get('id')}: {e!r}", file=sys.stderr)
                continue
            if m:
                matches.append(m)
    if not matches:
        print("FAIL no fixtures fetched", file=sys.stderr)
        return 2
    if len(failed) > len(LEAGUES) * len(days) // 2:
        print(f"FAIL {len(failed)} of {len(LEAGUES) * len(days)} scoreboard requests failed", file=sys.stderr)
        return 3

    wanted = missing = 0
    if args.no_logos:
        cache = {}
    elif args.embed_images:
        cache = load_logo_cache(args.logos)
        wanted, missing = fetch_logos(matches, cache, args.workers, league_logos)
        save_logo_cache(args.logos, cache)
    else:
        cache = image_links(matches, league_logos)

    page = build_page(matches, cache, built_at, failed, today)
    trimmed = ""
    if len(page.encode("utf-8")) > PAGE_BUDGET:
        # First without headshots and league logos, then also without logos for background leagues.
        slim = {k: v for k, v in cache.items() if not k.startswith(("h", "L"))}
        page = build_page(matches, slim, built_at, failed, today)
        trimmed = "headshots+league logos"
        if len(page.encode("utf-8")) > PAGE_BUDGET:
            keep = {t.logo_key for m in matches if LEAGUES[m.league]["tier"] < 3 for t in (m.home, m.away)}
            slim = {k: v for k, v in slim.items() if k in keep}
            page = build_page(matches, slim, built_at, failed, today)
            trimmed = "headshots+league logos+tier-3 team logos"
    if not args.fragment:
        page = as_document(page)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    n_on = sum(1 for m in matches if m.service)
    image_mode = "none" if args.no_logos else ("embedded" if args.embed_images else "linked")
    print(f"OK matches={len(matches)} on_services={n_on} days={days[0]}..{days[-1]} images={image_mode} logos_new={wanted} logos_missing={missing} tables={len(STANDINGS)} "
          f"fetch_failures={len(failed)} bytes={len(page.encode('utf-8'))} trimmed={trimmed or 'none'} built={built_at.astimezone(ET).strftime('%Y-%m-%d %H:%M ET')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
