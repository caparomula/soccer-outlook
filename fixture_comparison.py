"""Read-only checks of ESPN's fixtures against an independent dated scoreboard.

The initial, deliberately explicit scope is Liga MX. FOX fixture records are used only
for identity and kickoff comparisons: no broadcast, service entitlement or live state
is inferred from them. Discrepancies are observations, never automatic imports.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re
import unicodedata
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fox_listings import _Payload


ET = ZoneInfo("America/New_York")
SCORES_URL = "https://www.foxsports.com/soccer/liga-mx/scores?date={day}"
MAX_DATES = 6
MAX_PAGE_BYTES = 2_000_000
LEAGUE = "mex.1"
SOURCE = "FOX Sports fixtures"

# FOX's dated Liga MX scoreboards, cross-checked against ESPN's fixture participants
# on 2026-10-15. Keys are FOX team URI suffixes; values are ESPN IDs and explicit
# name variants. A new/renamed club remains unmapped until its identity is reviewed.
TEAM_IDS = {
    "1352": ("229", ("Necaxa",)), "99": ("216", ("Atlas",)),
    "114": ("10125", ("Tijuana", "Club Tijuana")),
    "106": ("231", ("Puebla",)), "98": ("226", ("Atlante",)),
    "110": ("233", ("Pumas", "Pumas UNAM", "UNAM Pumas")),
    "107": ("225", ("Santos", "Santos Laguna")),
    "112": ("222", ("Querétaro",)), "102": ("219", ("Guadalajara",)),
    "109": ("232", ("Tigres", "Tigres UANL")),
    "103": ("228", ("León",)), "100": ("227", ("América", "Club América")),
    "108": ("223", ("Toluca",)), "1633": ("15720", ("Atlético de San Luis",)),
    "101": ("218", ("Cruz Azul",)), "1926": ("17851", ("FC Juárez",)),
    "104": ("220", ("Monterrey",)), "111": ("234", ("Pachuca",)),
}


def _name(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value).casefold()
                            if not unicodedata.combining(c)).split())


ALIASES = {_name(name): espn for espn, names in TEAM_IDS.values() for name in names}


def _instant(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _day(value):
    if isinstance(value, datetime):
        return value.astimezone(ET).date() if value.utcoffset() is not None else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


@dataclass(frozen=True)
class Fixture:
    source_id: str
    home_id: str
    away_id: str
    home: str
    away: str
    utc: datetime
    url: str

    def summary(self):
        return dict(source_id=self.source_id, home_id=self.home_id, away_id=self.away_id,
                    home=self.home, away=self.away, kickoff=self.utc.isoformat(), url=self.url)


def parse_fox(page, day):
    """Return dated fixture evidence; an ignored date is not an empty schedule.

    Read only the selected Liga MX segment, not incidental cards elsewhere in the
    Nuxt table. Incomplete identities prevent negative claims about that date.
    """
    day = _day(day)
    if not isinstance(page, (bytes, str)) or len(page) > MAX_PAGE_BYTES:
        raise ValueError("FOX fixture page is missing or too large")
    if isinstance(page, bytes):
        page = page.decode("utf-8")
    parser = _Payload()
    parser.feed(page)
    if parser.count != 1 or not parser.parts:
        raise ValueError("FOX fixture page has no unique JSON payload")
    table = json.loads("".join(parser.parts))
    if not isinstance(table, list) or len(table) > 20_000:
        raise ValueError("FOX fixture page has no bounded reference table")

    def ref(value):
        return table[value] if type(value) is int and 0 <= value < len(table) else None

    def field(obj, key):
        return ref(obj.get(key)) if isinstance(obj, dict) else None

    def text(obj, key):
        value = field(obj, key)
        return value if isinstance(value, str) and len(value) <= 512 else ""

    scopes = [obj for obj in table if text(obj, "contentUri") == "soccer/liga_mx/league/6"
              and isinstance(field(obj, "scoresData"), dict)]
    if len(scopes) != 1:
        raise ValueError("FOX fixture page has no unique Liga MX schedule")
    schedule = field(scopes[0], "scoresData")
    segment = field(schedule, "parsedSegmentRes")
    segment_id = "c6d" + day.strftime("%Y%m%d")
    selected = text(schedule, "currentSegmentId")
    actual = text(field(segment, "segment"), "id")
    result = dict(status="ok", requested_date=day.isoformat(), selected_segment=selected,
                  observed_dates=[], complete=False, fixtures=[], rejected=[])
    if selected != segment_id or actual != segment_id:
        result["status"] = "outside_source_horizon"
        return result
    entries = field(segment, "events")
    if not isinstance(entries, list) or len(entries) > 200:
        raise ValueError("FOX fixture segment has no bounded event list")
    for pointer in entries:
        event = ref(pointer)
        content = text(event, "contentUri")
        identity = re.fullmatch(r"soccer/liga_mx/events/(\d+)", content)
        reason = "invalid_fixture_identity"
        if (not identity or text(event, "template") != "scores-team" or
                text(event, "contentType") != "event" or text(event, "league") != "LIGA MX" or
                field(event, "isReplay") is True):
            result["rejected"].append(dict(source_id=content, reason=reason))
            continue
        kickoff = _instant(text(event, "eventTime"))
        if kickoff is None or field(event, "isTba") is not False:
            result["rejected"].append(dict(source_id=content, reason="unconfirmed_kickoff"))
            continue
        observed_day = kickoff.astimezone(ET).date().isoformat()
        result["observed_dates"].append(observed_day)
        if observed_day != day.isoformat():
            result["rejected"].append(dict(source_id=content, reason="event_outside_requested_date"))
            continue
        link = field(event, "entityLink")
        tokens = field(field(link, "layout"), "tokens")
        if text(link, "contentUri") != content or text(tokens, "eventUri") != content:
            result["rejected"].append(dict(source_id=content, reason=reason))
            continue
        teams = [field(event, "upperTeam"), field(event, "lowerTeam")]
        participants = []
        for side in ("home", "away"):
            uri = text(tokens, side + "Uri")
            team_id = re.fullmatch(r"soccer/all/teams/(\d+)", uri)
            matching = [team for team in teams if text(team, "uri") == uri]
            if not team_id or len(matching) != 1:
                break
            name = text(matching[0], "longName")
            known = TEAM_IDS.get(team_id.group(1))
            if not known or _name(name) not in {_name(v) for v in known[1]}:
                break
            participants.append((known[0], name))
        if len(participants) != 2 or participants[0][0] == participants[1][0]:
            result["rejected"].append(dict(source_id=content, reason="unmapped_team_identity"))
            continue
        url = text(link, "webUrl")
        if url.startswith("/") and not url.startswith("//"):
            url = "https://www.foxsports.com" + url
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "www.foxsports.com" or parsed.query or
                parsed.fragment or not re.fullmatch(
                    r"/soccer/[a-z0-9-]+-game-boxscore-" + identity.group(1), parsed.path)):
            result["rejected"].append(dict(source_id=content, reason="invalid_source_url"))
            continue
        result["fixtures"].append(Fixture(content, participants[0][0], participants[1][0],
                                          participants[0][1], participants[1][1], kickoff, url))
    result["observed_dates"] = sorted(set(result["observed_dates"]))
    result["complete"] = not result["rejected"]
    if not result["complete"]:
        result["status"] = "incomplete"
    return result


def _primary_team(team):
    # IDs are authoritative. Alias fallback is only for an absent ID, never a
    # conflicting ID or a new club with a familiar-looking display name.
    identifier = str(getattr(team, "id", "") or "")
    if identifier:
        return identifier if identifier in {item[0] for item in TEAM_IDS.values()} else ""
    return ALIASES.get(_name(team.name), "")


def compare(matches, dates, fetch, *, observed_at=None):
    """Compare without mutating matches. ``fetch(url)`` owns timeout/retry/cache.

    At most six dated FOX pages are requested, even when ESPN has no Liga MX
    fixtures. This is necessary to detect omissions in the primary feed. Missing
    from a secondary source means absent in a successfully parsed date, not that
    the fixture is cancelled. Unsupported leagues are explicitly out of scope.
    """
    now = observed_at or datetime.now(timezone.utc)
    if isinstance(now, str):
        now = _instant(now)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("Fixture comparisons need an aware observation time")
    now = now.astimezone(timezone.utc)
    matches = list(matches)
    days = sorted({_day(value) for value in dates})
    chosen, skipped = days[:MAX_DATES], days[MAX_DATES:]
    report = dict(version=1, observed_at=now.isoformat(), mode="observation_only",
                  scope={LEAGUE: SOURCE}, requested=0, failed=0, sources=[], matched=[],
                  missing_from_espn=[], missing_from_secondary=[], kickoff_conflicts=[],
                  ambiguous=[], unmapped=[], unknown=[],
                  unsupported_leagues=sorted({m.league for m in matches if m.league != LEAGUE}),
                  skipped_dates=[day.isoformat() for day in skipped])
    primary = defaultdict(list)
    for match in matches:
        if match.league != LEAGUE or match.state not in ("pre", "in"):
            continue
        if not match.time_valid or match.utc.utcoffset() is None:
            report["unknown"].append(dict(espn_id=match.id, reason="unconfirmed_primary_kickoff"))
            continue
        day = match.utc.astimezone(ET).date()
        if day not in chosen:
            continue
        home, away = _primary_team(match.home), _primary_team(match.away)
        if not home or not away or home == away:
            report["unmapped"].append(dict(espn_id=match.id, date=day.isoformat(),
                                            reason="unmapped_primary_team_identity"))
            continue
        primary[day].append((match, home, away))
    all_rows = []
    source_dates = {}
    for day in chosen:
        url = SCORES_URL.format(day=day.isoformat())
        report["requested"] += 1
        try:
            page = fetch(url)
        except Exception:
            page = None
        try:
            source = parse_fox(page, day)
        except (ValueError, UnicodeError, AssertionError, RecursionError):
            source = dict(status="unavailable", requested_date=day.isoformat(),
                          selected_segment="", observed_dates=[], complete=False,
                          fixtures=[], rejected=[])
            report["failed"] += 1
        source_rows = source.pop("fixtures")
        report["sources"].append(dict(source=SOURCE, league=LEAGUE, url=url,
                                      observed_at=now.isoformat(), records=len(source_rows), **source))
        for rejected in source["rejected"]:
            report["unmapped"].append(dict(source=SOURCE, date=day.isoformat(), **rejected))
        source_dates[day] = source
        all_rows.extend(source_rows)
    # Compare identities across the whole inspected window. A unique home/away
    # pairing shifted across midnight is a kickoff conflict, not two omissions.
    rows, games = defaultdict(list), defaultdict(list)
    source_ids = Counter(item.source_id for item in all_rows)
    for item in all_rows:
        rows[(item.home_id, item.away_id)].append(item)
    for day_games in primary.values():
        for match, home, away in day_games:
            games[(home, away)].append(match)
    for pair in sorted(set(rows) | set(games)):
        found, candidates = rows[pair], games[pair]
        if (len(found) > 1 or len(candidates) > 1 or
                any(source_ids[item.source_id] > 1 for item in found)):
            report["ambiguous"].append(dict(home_id=pair[0], away_id=pair[1],
                source_ids=[item.source_id for item in found], espn_ids=[m.id for m in candidates]))
            continue
        if found and candidates:
            item, match = found[0], candidates[0]
            entry = dict(league=LEAGUE, espn_id=match.id, **item.summary())
            if item.utc == match.utc:
                report["matched"].append(entry)
            else:
                report["kickoff_conflicts"].append(dict(entry, espn_kickoff=match.utc.isoformat(),
                    difference_minutes=(item.utc - match.utc).total_seconds() / 60))
        elif candidates:
            match = candidates[0]
            day = match.utc.astimezone(ET).date()
            source = source_dates[day]
            entry = dict(league=LEAGUE, espn_id=match.id, date=day.isoformat(),
                         home=match.home.name, away=match.away.name,
                         url=SCORES_URL.format(day=day.isoformat()))
            if source["complete"]:
                report["missing_from_secondary"].append(dict(entry, reason="absent_from_observed_date"))
            else:
                reason = (source["status"] if source["status"] in ("unavailable", "outside_source_horizon")
                          else "secondary_parse_incomplete")
                report["unknown"].append(dict(entry, reason=reason))
        elif found and found[0].utc >= now:
            item = found[0]
            # A completed ESPN fixture or an unresolved primary identity/time can
            # explain a source-only row. Do not manufacture an omission in either case.
            if any(m.league == LEAGUE and m.state == "post" and
                   (_primary_team(m.home), _primary_team(m.away)) == pair for m in matches):
                continue
            primary_unknown = [m for m in matches if m.league == LEAGUE and m.state in ("pre", "in")
                               and (not m.time_valid or m.utc.utcoffset() is None or
                                    not _primary_team(m.home) or not _primary_team(m.away))]
            if primary_unknown:
                report["unknown"].append(dict(item.summary(), reason="primary_identity_incomplete"))
                continue
            report["missing_from_espn"].append(dict(league=LEAGUE, **item.summary()))
    report["counts"] = {key: len(report[key]) for key in (
        "matched", "missing_from_espn", "missing_from_secondary", "kickoff_conflicts",
        "ambiguous", "unmapped", "unknown")}
    report["complete"] = (not skipped and not report["unmapped"] and not report["unknown"] and
                          not report["ambiguous"] and all(source["complete"] for source in report["sources"]))
    return report
