"""Small, crawlable US viewing schedules, built from the app's fixtures and rights mappings.

These pages are independent of a visitor's saved filters. They need no JavaScript and make no
extra data requests during the build. The interactive app remains the place for personal picks,
live updates, and matches across the tracked leagues and services.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from html import escape
from pathlib import Path
import tomllib
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Route:
    slug: str
    label: str
    title: str
    description: str
    league: str = ""
    service: str = ""


ROUTES = (
    Route("premier-league/", "Premier League", "Premier League TV Schedule in the US",
          "Find Premier League matches on US TV and streaming today and over the next three days, "
          "with Eastern kickoff times and broadcaster listings.", league="eng.1"),
    Route("mls/", "MLS", "MLS TV & Streaming Schedule in the US",
          "Find MLS matches on US TV and streaming today and over the next three days, "
          "with Eastern kickoff times and listed or usual viewing options.", league="usa.1"),
    Route("paramount-plus/", "Paramount+", "Soccer on Paramount+ — US Match Schedule",
          "Find soccer matches available through Paramount+ in the US today and over the next "
          "three days, with Eastern kickoff times and coverage details.", service="para"),
)


def navigation(site_url, current=""):
    """Ordinary links let both people and crawlers move between the useful public schedules."""
    base = site_url.rstrip("/") + "/"
    links = []
    for route in ROUTES:
        here = ' aria-current="page"' if route.slug == current else ""
        links.append(f'<a href="{escape(base + route.slug, quote=True)}"{here}>{escape(route.label)}</a>')
    return '<nav class="schedule-links" aria-label="US soccer schedules">' + " ".join(links) + "</nav>"


def sports_day(moment, tz):
    """An Eastern schedule day runs from 4 am until 4 am on the next calendar date."""
    local = moment.astimezone(tz)
    return local.date() - timedelta(days=local.hour < 4)


def _safe_link(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    return value if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password else ""


def _coverage(builder, route, match):
    """Resolve the page's own viewing context, never the owner's preferred service on a match."""
    listed = [outlet for outlet in match.outlets if outlet.known]
    rule = match.rule if match.rule and match.rule.known else None
    if route.service:
        sid, basis, outlet = builder.evaluate(listed, rule, {route.service})
        if not sid:
            return ""
        service = builder.SERVICES[sid]
        via = service if outlet == service else f"{service} via {outlet}"
        return f"Usually {via}; match listing not yet confirmed" if basis == "rule" else via
    if listed:
        return " · ".join(dict.fromkeys(outlet.label for outlet in listed))
    if rule:
        return f"Usually {rule.label}; match listing not yet confirmed"
    return ""


def _guidance(builder, route, built_at, service_notes):
    if route.service:
        paragraphs = ["This schedule includes tracked competitions with a listed route through Paramount+ "
                      "or an established usual home there. It does not assume every match on a CBS sports "
                      "channel is available through Paramount+."]
        if service_notes.get(route.service):
            paragraphs.append(service_notes[route.service])
        return paragraphs
    rights = builder.RIGHTS.leagues.get(route.league)
    paragraphs = []
    if rights and rights.usual and built_at.astimezone(builder.ET).date() <= rights.usual.until:
        if rights.usual.channel:
            paragraphs.append(f"The established US home for {route.label} is {rights.usual.channel} "
                              f"for the {rights.usual.season} season. Individual listings take precedence.")
    if rights and rights.hint:
        paragraphs.append(f"US viewing guide: {rights.hint}.")
    if not paragraphs:
        paragraphs.append("US viewing options vary by match. Use the broadcaster shown beside each fixture.")
    return paragraphs


def _match_html(builder, match, coverage):
    esc = escape
    local = match.utc.astimezone(builder.ET)
    calendar_day = local.strftime("%a, %b ") + str(local.day)
    if match.time_valid:
        clock = local.strftime("%I:%M %p").lstrip("0").lower()
        time_html = (f'<time datetime="{esc(match.utc.isoformat(), quote=True)}">'
                     f'<span class="kickoff-date">{calendar_day}</span><strong>{clock}</strong> ET</time>')
    else:
        time_html = f'<span class="kickoff-date">{calendar_day}</span><strong>Time TBD</strong>'
    teams = f"{match.home.name} v {match.away.name}"
    meta = " · ".join(part for part in (match.comp, match.stage, match.venue) if part)
    status = ""
    if match.state in {"in", "post"}:
        scores = f"{match.home.score}–{match.away.score}" if match.home.score != "" and match.away.score != "" else ""
        label = "In progress at last update" if match.state == "in" else "Final"
        if match.status and match.status not in {"FT", "Final", "Live"}:
            label += f" · {match.status}"
        status = f'<p class="match-status">{esc(" · ".join(part for part in (label, scores) if part))}</p>'
    note = f'<p class="match-note">{esc(match.note)}</p>' if match.note else ""
    source = _safe_link(match.link)
    source_html = f'<a class="match-source" href="{esc(source, quote=True)}">ESPN match details</a>' if source else ""
    return (f'<li class="match" data-match-id="{esc(str(match.id), quote=True)}">'
            f'<div class="match-time">{time_html}</div><div class="match-info">'
            f'<h3>{esc(teams)}</h3><p class="match-meta">{esc(meta)}</p>{status}{note}'
            f'<p class="match-coverage">{esc(coverage)}</p>{source_html}</div></li>')


def pages(builder, matches, cache, built_at, site_url, failed=(), skipped=(), head_extra="", schedule_day=None):
    """Return {trailing-slash route: full HTML}; the caller writes each to route/index.html.

    `builder` supplies the existing rights evaluator and data model without a circular import.
    `cache` is accepted for parity with the main renderer; these compact schedules use text.
    `head_extra` is trusted markup from the caller, for validated site-verification metadata.
    `schedule_day` is the builder's explicit --date override for historical test schedules.
    """
    base = builder.public_site_url(site_url)
    styles = (Path(__file__).parent / "web" / "discovery.css").read_text(encoding="utf-8")
    with open(builder.RIGHTS_PATH, "rb") as handle:
        service_notes = {sid: value.get("note", "") for sid, value in tomllib.load(handle)["services"].items()}
    first_day = schedule_day if schedule_day is not None else sports_day(built_at, builder.ET)
    last_day = first_day + timedelta(days=3)
    built_local = built_at.astimezone(builder.ET)
    updated = built_local.strftime("%B ") + str(built_local.day) + built_local.strftime(", %Y at %I:%M %p %Z").replace(" 0", " ")
    result = {}
    for route in ROUTES:
        grouped = defaultdict(list)
        unknown = 0
        for match in sorted(matches, key=lambda m: (m.utc, m.comp, m.home.name, m.id)):
            day = sports_day(match.utc, builder.ET)
            if not first_day <= day <= last_day or builder.called_off(match.state, match.status):
                continue
            if route.league and match.league != route.league:
                continue
            coverage = _coverage(builder, route, match)
            if coverage:
                grouped[day].append(_match_html(builder, match, coverage))
            elif route.league:
                unknown += 1
        relevant_failed = [entry for entry in failed if not route.league or entry[0] == route.league]
        relevant_skipped = [entry for entry in skipped if not route.league or entry.get("league") == route.league]
        warning = ('<p class="source-warning">Some ESPN data could not be loaded or read. This schedule may be '
                   'incomplete; check the broadcaster before making plans.</p>' if relevant_failed or relevant_skipped else "")
        sections = []
        for day in (first_day + timedelta(days=i) for i in range(4)):
            label = {first_day: "Today", first_day + timedelta(days=1): "Tomorrow"}.get(day, day.strftime("%A"))
            day_label = day.strftime("%A, %B ") + str(day.day)
            body = ('<ol class="matches">' + "".join(grouped[day]) + '</ol>' if grouped[day] else
                    '<p class="empty-day">No matches with listed US coverage or an established usual home in this snapshot.</p>')
            sections.append(f'<section class="schedule-day"><h2>{label}<span>{day_label}</span></h2>{body}</section>')
        if unknown:
            noun = "fixture has" if unknown == 1 else "fixtures have"
            sections.append(f'<p class="coverage-note">{unknown} other {noun} no known US viewing option in this window. '
                            'Coverage may be added when broadcaster listings arrive.</p>')
        guidance = "".join(f"<p>{escape(text)}</p>" for text in _guidance(builder, route, built_at, service_notes))
        canonical = escape(base + route.slug, quote=True)
        title = escape(route.title + " | Soccer Outlook")
        description = escape(route.description, quote=True)
        page = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<link rel="sitemap" type="application/xml" href="{escape(base, quote=True)}sitemap.xml">
<link rel="icon" type="image/png" sizes="64x64" href="{escape(base, quote=True)}favicon.png">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Soccer Outlook">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta name="color-scheme" content="light dark">
{head_extra}
<style>{styles}</style>
</head>
<body>
<a class="skip-link" href="#schedule">Skip to matches</a>
<div class="page">
<header class="masthead"><a class="site-name" href="{escape(base, quote=True)}">Soccer Outlook</a>
<a class="app-link" href="{escape(base, quote=True)}">Personalize your schedule <span aria-hidden="true">→</span></a></header>
<main>
<div class="intro"><p class="eyebrow">US TV &amp; streaming · Today + three days</p>
<h1>{escape(route.title)}</h1><p class="description">{escape(route.description)}</p>
<p class="updated">Updated <time datetime="{escape(built_at.isoformat(), quote=True)}">{updated}</time>.</p></div>
{navigation(base, route.slug)}
<p class="schedule-guide">All kickoff times are Eastern.</p>
{warning}
<div id="schedule" tabindex="-1">{"".join(sections)}</div>
<div class="schedule-guide"><p>Schedule days run from 4 am to 4 am; late-night matches show their actual calendar date.
“Usually” means established rights, with the match listing still unconfirmed.</p>
<p>This is a schedule snapshot. <a href="{escape(base, quote=True)}">Open the app</a> for live updates, local times and your own service filters.</p></div>
<section class="viewing-guide"><h2>How to watch {escape(route.label) if route.league else "soccer on Paramount+"} in the US</h2>{guidance}
<p>Broadcasters and kickoff times can change. Check your provider for availability and subscription requirements.</p></section>
</main>
<footer><p>Fixtures and broadcaster listings from ESPN. Refreshed three times a day.</p>
<p><a href="{escape(base, quote=True)}">All leagues and services</a> · <a href="https://github.com/caparomula/soccer-outlook">About this open-source project</a></p></footer>
</div>
</body>
</html>
'''
        result[route.slug] = page
    return result
