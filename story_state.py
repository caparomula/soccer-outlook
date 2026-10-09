"""Pure window and reuse rules for AI generation; no provider calls or file writes."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
WINDOW_DAYS = 3
DAY_START_HOUR = 4
MIN_GAP_HOURS = 3
MAX_AGE_HOURS = 30
BLURB_CANDIDATES = 6  # The top three plus room for different league priorities or a live card.
COMPONENTS = ("popularity", "gameplay", "impact")


def window_end(built):
    """4 am Eastern after today and the three following days; days start at 4 am."""
    day = (built.astimezone(ET) - timedelta(hours=DAY_START_HOUR)).date()
    return datetime.combine(day + timedelta(days=WINDOW_DAYS + 1), time(DAY_START_HOUR), tzinfo=ET)


def rating_window(facts):
    """The fixtures inside the page's Eastern display window and its length in hours."""
    built = datetime.fromisoformat(facts["built_at"].replace("Z", "+00:00"))
    end = window_end(built)

    def before(match):
        try:
            return datetime.fromisoformat(match["kickoff_utc"].replace("Z", "+00:00")) < end
        except (KeyError, AttributeError, TypeError, ValueError):
            return True  # Preserve fixtures whose kickoff cannot be read.
    return [m for m in facts.get("ranking_candidates", []) if before(m)], round((end - built).total_seconds() / 3600, 1)


def valid_rating(value):
    return isinstance(value, dict) and all(type(value.get(key)) is int and 0 <= value[key] <= 100 for key in COMPONENTS)


def missing_ratings(facts, previous):
    """Newly listed, newly in-window, or previously unrated fixtures, in schedule order."""
    ratings = (previous or {}).get("rankings") or {}
    return [m["id"] for m in rating_window(facts)[0] if not valid_rating(ratings.get(m["id"]))]


def story_age_hours(story, now):
    try:
        written = datetime.fromisoformat(story["generated_at"].replace("Z", "+00:00"))
        return (now - written).total_seconds() / 3600
    except (KeyError, AttributeError, TypeError, ValueError):
        return None


def still_fresh(story, now):
    """The browser's freshness rules, for a safe deployment fallback."""
    age = story_age_hours(story, now)
    if age is None or age < -1 or age >= MAX_AGE_HOURS:
        return False
    if story.get("focus_until"):
        try:
            return datetime.fromisoformat(story["focus_until"].replace("Z", "+00:00")) > now
        except (TypeError, ValueError, AttributeError):
            return False
    return True


def choose_mode(requested, todays, now, design="full", switched=False):
    """Choose the base generation mode. Daily ratings may then need a missing-fixture top-up."""
    if requested == "keep":
        if switched:
            return ("ratings" if design == "ratings" else "full"), "settings.toml names another design or model than the published story's"
        return "keep", ""
    if design == "ratings":
        if requested in ("daily", "auto"):
            return ("keep", "today's ratings are written") if todays else ("ratings", "no ratings for today yet")
        return "ratings", ("" if requested == "ratings" else f"the ratings design rates when asked for {requested}")
    if requested == "daily":
        return ("keep", "today's storylines are written; they are written once a day") if todays else ("full", "no storylines for today yet")
    if requested == "auto":
        age = story_age_hours(todays, now) if todays else None
        if age is None:
            return "full", "no storylines for today yet"
        if age < MIN_GAP_HOURS:
            return "keep", f"today's storylines are {age * 60:.0f} minutes old"
        return "refresh", f"today's storylines are {age:.1f} hours old"
    if requested == "refresh" and not todays:
        return "full", "no storylines for today yet, so this refresh writes them from scratch"
    return requested, ""
