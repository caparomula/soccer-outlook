"""Validate optional, sourced post-match measurements for the historical archive.

No measurements are inferred or fetched here. Each entry retains its own definition
and scope so television audiences, stadium attendance and video views stay distinct.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit


UNITS = {"attendance": "people", "stadium_capacity": "seats", "average_viewers": "viewers",
         "peak_viewers": "viewers", "unique_viewers": "viewers", "highlights_views": "views",
         "search_interest": "index", "news_articles": "articles"}


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is not None:
            return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("observation timestamps must include a date, time and timezone")


def validate(data, captured_at):
    if not isinstance(data, dict) or set(data) != {"version", "observations"} or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("observations require version 1 and an observations array")
    rows = data["observations"]
    if not isinstance(rows, list):
        raise ValueError("observations must be an array")
    seen = set()
    required = {"id", "match_id", "metric", "value", "unit", "observed_at", "source_url", "scope"}
    optional = {"league_id", "source_title", "source_published_at", "window_start", "window_end", "notes"}
    for row in rows:
        if not isinstance(row, dict) or not required <= row.keys() or row.keys() - required - optional:
            raise ValueError("missing or unknown observation fields")
        for key in ("id", "match_id"):
            if not isinstance(row[key], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", row[key]):
                raise ValueError("invalid observation or match ID")
        if row["id"] in seen:
            raise ValueError("duplicate observation ID")
        seen.add(row["id"])
        metric, value = row["metric"], row["value"]
        if not isinstance(metric, str) or metric not in UNITS or row["unit"] != UNITS[metric]:
            raise ValueError("unsupported metric or incorrect unit")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("observation must have a finite nonnegative value")
        if metric == "search_interest" and value > 100:
            raise ValueError("search interest must be on a 0–100 scale")
        if metric != "search_interest" and int(value) != value:
            raise ValueError("count measurements must be whole numbers")
        observed = timestamp(row["observed_at"])
        if observed > captured_at:
            raise ValueError("observation is in the future")
        for key in ("scope", "source_url", "source_title", "league_id", "notes"):
            if key in row and (not isinstance(row[key], str) or not row[key].strip() or len(row[key]) > 2000):
                raise ValueError("observation text must be nonempty and at most 2000 characters")
        url = urlsplit(row["source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("observation source must be a public HTTPS URL")
        if "source_published_at" in row and timestamp(row["source_published_at"]) > observed:
            raise ValueError("source publication cannot follow observation time")
        if ("window_start" in row) != ("window_end" in row):
            raise ValueError("measurement windows require both start and end")
        if "window_start" in row and not timestamp(row["window_start"]) <= timestamp(row["window_end"]) <= observed:
            raise ValueError("measurement window must end no later than observation")
        if metric in ("highlights_views", "search_interest", "news_articles") and "window_start" not in row:
            raise ValueError("video, search and news counts require a measurement window")
    return deepcopy(rows)


def load(path, captured_at):
    if path is None or not Path(path).exists():
        return []
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate observation JSON field")
            result[key] = value
        return result
    with Path(path).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("observations file exceeds 1 MiB")
    return validate(json.loads(raw, object_pairs_hook=unique), captured_at)
