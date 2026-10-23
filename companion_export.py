"""Opt-in companion facts exporter; never selects a visitor's viewing lineup.

The existing builder owns source interpretation, rights and Outlook calculation.
This adapter projects those facts into the preview contract without I/O to sources.
"""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import story_state
import outlook_runtime
from provider_utils import public_https as https

ROOT = Path(__file__).resolve().parent
POLICY_ID = "outlook-policy-p1-preview-1"
NETWORKS = json.loads((ROOT / "contracts/outlook/network-ids.json").read_text())
NETWORK_IDS = {name: value["id"] for name, value in NETWORKS.items()}


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def revision(kind, value):
    return kind + ":" + hashlib.sha256(canonical(value)).hexdigest()


def envelope(kind, **fields):
    value = dict(schema_version=1, kind="outlook_" + kind, **fields)
    value["content_revision"] = revision(kind, value)
    return value


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("An explicit observation/publication instant is required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parsed(value):
    return datetime.fromisoformat(instant(value).replace("Z", "+00:00"))


def source_id(value):
    return "source:" + hashlib.sha256(str(value).encode()).hexdigest()[:24]


def number(value, *, maximum=100):
    # Missing, presentation markup and pre-game placeholders must not become zero.
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        return None
    result = int(value)
    return result if 0 <= result <= maximum else None


def observation(received, source="espn"):
    return dict(source=source, observed_at=None, provider_updated_at=None,
                received_at=instant(received))


def reported_form(raw):
    """Normalize source encoding, not the truth or as-of date of its results.

    The dated capture in tests/contracts/form demonstrates newest-first order;
    it also records contradictory values. Never replace those from another
    endpoint or imply that collection receipt is the form's observation time.
    """
    supported = isinstance(raw, str) and re.fullmatch(r"[WDL]{1,5}", raw) is not None
    return dict(form=list(reversed(raw)) if supported else [], raw_form=raw or None,
        form_order="oldest_to_newest", form_provenance=dict(
            source_order="newest_to_oldest" if supported else None,
            order_basis="espn-dated-comparison-2026-10-20" if supported else None,
            values_verified=False, as_of=None, competition_scope=None))


def source_scopes(report, built_at):
    scopes = deepcopy(report.get("source_scopes"))
    if not scopes:
        # Historical captured coverage retains each successful request. Its
        # original generated_at is supplied by the replay caller, never now().
        sources = report.get("sources", report.get("fixture_sources", {}))
        records = sources.get("details", []) if isinstance(sources, dict) else []
        by_league = {}
        for entry in records:
            league = entry.get("league")
            if not league or not entry.get("date"):
                raise ValueError("Fixture scope lacks league/date identity")
            group = by_league.setdefault(league, [])
            group.append(entry)
        scopes = [dict(source="espn", league_id=league, date_zone="America/New_York",
                       requested_dates=sorted({e["date"] for e in entries}),
                       covered_dates=sorted({e["date"] for e in entries if e.get("ok") is True}),
                       complete=all(e.get("ok") is True for e in entries),
                       attempted_at=instant(built_at), validated_at=instant(built_at))
                  for league, entries in sorted(by_league.items())]
    if report.get("complete", report.get("fixture_sources_complete")) is not True or not scopes:
        raise ValueError("Incomplete fixture collection cannot replace the companion feed")
    for scope in scopes:
        if (scope.get("complete") is not True or not scope.get("requested_dates") or
                set(scope["requested_dates"]) != set(scope.get("covered_dates", []))):
            raise ValueError("Incomplete fixture date scope")
        try:
            ZoneInfo(scope["date_zone"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("Unknown fixture scope time zone") from None
        scope["attempted_at"] = instant(scope["attempted_at"])
        scope["validated_at"] = instant(scope["validated_at"])
    return scopes


def export_artifacts(builder, matches, built_at, report, *, standings_at=None):
    """Project a complete build's unfiltered Match values; no publication writes."""
    matches = list(matches)
    built_at = parsed(built_at)
    standings_at = parsed(standings_at or built_at)
    scopes = source_scopes(report, built_at)
    scope_by_league = {s["league_id"]: s for s in scopes}
    if len(scope_by_league) != len(scopes):
        raise ValueError("Duplicate fixture scope")
    rights = tomllib.loads(Path(builder.RIGHTS_PATH).read_text())
    if set(rights["channels"]) - NETWORK_IDS.keys():
        raise ValueError("Register new canonical network IDs before exporting")
    assets, teams, vitals, fixtures, evidence, destinations = {}, {}, [], [], [], []

    def asset(url):
        url = https(url)
        if not url:
            return None
        suffix = Path(urlsplit(url).path).suffix.lower()
        media = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
        if not media:
            return None
        key = revision("asset", url)
        assets[key] = dict(id=key, location=url, media_type=media, width=None,
                           height=None, attribution=None, contrast=None)
        return key

    leagues = [dict(id=key, name=info["name"], source_refs=[dict(source="espn", id=key)],
                    asset_id=asset(builder.LEAGUE_LOGOS.get(key)),
                    abbreviation=info.get("short"), description=info.get("about"))
               for key, info in builder.LEAGUES.items()]
    plans = [dict(id=key, name=name, notes=[rights["services"][key]["note"]]
                  if rights["services"][key].get("note") else [], retired=False)
             for key, name in builder.SERVICES.items()]
    networks = [dict(id=NETWORK_IDS[name], name=name, aliases=list(info.get("espn", [])),
                     asset_id=None) for name, info in rights["channels"].items()]
    policy = envelope("policy", policy_id=POLICY_ID,
        defaults=dict(league_order=list(builder.LEAGUES),
                      enabled_league_ids=[k for k, v in builder.LEAGUES.items() if not v.get("default_off")],
                      service_plan_order=list(builder.SERVICE_RANK),
                      enabled_service_plan_ids=list(builder.OWNER), ai_text=False),
        rules=dict(eligibility="watch-routes-v1-draft", sports_day="local-sports-day-v1-draft",
                   ranking="personal-ranking-v1-draft", editorial="editorial-lifetime-v1-draft",
                   rating_dots="six-dot-rounding-v1-draft", freshness="source-freshness-v1-draft"),
        blend=dict(builder.SETTINGS.blend), window=dict(boundary_hour=4, past_days=3, future_days=3),
        freshness=dict(retained_evidence_max_seconds=21600, personal_shared_max_seconds=86400,
                       automatic_max_seconds=story_state.MAX_AGE_HOURS * 3600),
        semantics="preview; production live freshness and shared decisions remain P2/P3")
    for match in matches:
        fid = "espn:fixture:" + str(match.id)
        if match.league not in scope_by_league:
            raise ValueError("Fixture has no collection scope")
        scope = scope_by_league[match.league]
        if match.utc.astimezone(ZoneInfo(scope["date_zone"])).date().isoformat() not in scope["covered_dates"]:
            raise ValueError("Fixture falls outside its covered source dates")
        receipt = scope_by_league[match.league]["validated_at"]
        kickoff = instant(match.utc) if match.time_valid else None
        team_ids, vital_ids = [], []
        for side, team in (("home", match.home), ("away", match.away)):
            if not team.id:
                raise ValueError("Cannot invent a missing team identity")
            tid = "espn:team:" + str(team.id)
            team_ids.append(tid)
            teams[tid] = dict(id=tid, name=team.name, abbreviation=team.abbr or None,
                              source_refs=[dict(source="espn", id=str(team.id))],
                              asset_id=asset(team.logo_url), color=team.color or None,
                              alternate_color=team.alt or None)
            # Vitals from different dated scoreboards are not silently merged.
            vid = f"vitals:{match.id}:{side}"
            vital_ids.append(vid)
            rank = dict(position=team.rank, table_size=team.size, group=team.group or None) if team.rank > 0 and team.size > 0 else None
            vitals.append(dict(id=vid, team_id=tid, league_id=match.league,
                observation=observation(receipt), **reported_form(team.form),
                record=team.record or None, rank=rank, hometown=None,
                leader=dict(name=team.leader, goals=number(team.leader_goals, maximum=1000)) if team.leader else None,
                standings_received_at=instant(standings_at), points=team.pts or None))
        if team_ids[0] == team_ids[1]:
            raise ValueError("Fixture has the same team on both sides")
        seen_channels, routes, unresolved, destination_ids = [], [], [], []
        match_destinations = builder.watch_links.match_links(match)
        for link in match_destinations:
            url = https(link.get("url"))
            if not url:
                continue
            dest = dict(kind=link["kind"], url=url,
                        network_ids=[NETWORK_IDS[n] for n in link["channels"]],
                        service_plan_ids=list(link["via"]), country="US", fixture_id=fid,
                        live_only=bool(link.get("live_only")))
            dest["id"] = revision("destination", dest)
            destinations.append(dest)
            destination_ids.append(dest["id"])
        for outlet in [*match.outlets, *([match.rule] if match.rule else [])]:
            if not outlet.known:
                unresolved.append(outlet.label)
                continue
            nid = NETWORK_IDS[outlet.label]
            if nid in seen_channels:
                continue
            seen_channels.append(nid)
            is_usual = outlet is match.rule
            route_evidence, verified, retained, expiry, period = [], None, False, None, None
            records = [e for e in match.broadcast_evidence if e.get("channel") == outlet.label]
            if not is_usual and not records:
                # Existing Match outlets originate in the US scoreboard listing.
                records = [dict(channel=outlet.label, source="ESPN", url=https(match.link) or
                           builder.ESPN.format(league=match.league, day=match.utc.astimezone(builder.ET).strftime("%Y%m%d")),
                           checked_at=receipt, country="US", kind="match_listing")]
            for raw in records:
                url = https(raw.get("url"))
                if not url or not raw.get("checked_at"):
                    continue
                classification = "live_match" if raw.get("kind") == "match_listing" else "replay" if raw.get("kind") == "replay" else "unknown"
                accepted = classification == "live_match" and raw.get("country") == "US"
                source = source_id(raw.get("source", "unknown"))
                external_id = raw.get("source_event_id")
                source_refs = [dict(source=source, id=str(external_id))] if external_id is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", str(external_id)) else []
                ev = dict(source=source, source_url=url,
                          fixture_id=fid, team_ids=team_ids, kickoff=kickoff, source_refs=[],
                          country=raw.get("country"), package=raw.get("package"),
                          observed_at=instant(raw["checked_at"]), classification=classification,
                          verdict="accepted" if accepted else "rejected", reason=None if accepted else "Not a verified US live listing")
                ev["source_refs"] = source_refs
                for key in ("source_event_id", "authority", "broadcast_start", "broadcast_end", "availability_start", "availability_end"):
                    if isinstance(raw.get(key), (str, int)) and not isinstance(raw.get(key), bool):
                        ev[key] = raw[key]
                ev["id"] = revision("evidence", ev)
                evidence.append(ev)
                if accepted:
                    if ev["id"] not in route_evidence:
                        route_evidence.append(ev["id"])
                    verified = min(verified, ev["observed_at"], key=parsed) if verified else ev["observed_at"]
                    retained = retained or bool(raw.get("retained"))
            basis = "usual" if is_usual else "listed" if route_evidence else "unknown"
            if is_usual:
                claim = rights["leagues"][match.league]
                verified = instant(datetime.combine(claim["checked"], time(), timezone.utc))
                period = dict(start=claim["checked"].isoformat(), end_inclusive=claim["until"].isoformat())
            validity = builder.route_validity_facts(match, outlet, usual=is_usual, evidence=records)
            validity["basis"] = "rule" if basis == "usual" else "none" if basis == "unknown" else "listed"
            temporal = outlook_runtime.route_validity(validity, built_at)
            if not is_usual:
                retained, verified = validity["retained"], validity["verified_at"]
            # Rights-period-only routes retain their existing feed shape. A
            # retained or explicitly bounded listing publishes its fixed cutoff.
            if retained or validity["expires_at"] is not None:
                expiry = temporal["expires_at"]
            eligible = list(outlet.via) if temporal["usable"] else []
            notes = ["Rights verification has date precision"] if is_usual else []
            if retained and (verified is None or expiry is None or parsed(expiry) <= parsed(verified)):
                # The feed requires a valid fixed interval for retained routes.
                # Preserve evidence, but publish an unknown route rather than
                # inventing an original timestamp or making the claim fresh.
                basis, retained, verified, expiry, eligible = "unknown", False, None, None, []
                notes = ["Retained coverage lacks valid original timing."]
            routes.append(dict(id=revision("route", [fid, nid]), network_id=nid,
                eligible_service_plan_ids=eligible, country="US", scope=NETWORKS[outlet.label]["scope"],
                basis=basis, package_requirements=[],
                excluded_service_plan_ids=[p for p in builder.SERVICES if p not in eligible],
                notes=notes,
                evidence_ids=route_evidence, verified_at=verified, retained=retained,
                expires_at=expiry, rights_period=period,
                destination_ids=[d["id"] for d in destinations if d["fixture_id"] == fid and nid in d["network_ids"]]))
        status_facts = builder.match_status(match)
        parts = builder.outlook_parts(match)
        weight = builder.SETTINGS.outlook
        fixtures.append(dict(id=fid, league_id=match.league, home_team_id=team_ids[0], away_team_id=team_ids[1],
            source_refs=[dict(source="espn", id=str(match.id))],
            timing=dict(confirmed=bool(match.time_valid), kickoff=kickoff,
                        scheduled_date=match.utc.astimezone(builder.ET).date().isoformat(),
                        scheduled_date_zone="America/New_York" if match.time_valid else None,
                        unconfirmed_date_basis=None if match.time_valid else "provider placeholder normalized to collection date; zone unverified"),
            status=dict(state=status_facts["state"], provider_state=status_facts["provider_state"], provider_detail=status_facts["label"],
                        observation=observation(receipt),
                        scores=dict(match=status_facts["scores"], shootout=None, aggregate=None),
                        period=None, clock=status_facts["label"] or None if status_facts["state"] == "live" else None),
            context=dict(stage=match.stage or None, group=match.home.group or None,
                         venue=match.venue or None, place=None, notes=[match.note] if match.note else [],
                         home_vitals_id=vital_ids[0], away_vitals_id=vital_ids[1]),
            network_ids=seen_channels, routes=routes, unresolved_channels=unresolved,
            outlook=dict(total=builder.outlook_score(parts), components=parts,
                         raw_inputs=dict(stature=match.score, home_win=match.home_win, away_win=match.away_win,
                             goal_line=match.goal_line, home_rank=match.home.rank or None, away_rank=match.away.rank or None,
                             home_table_size=match.home.size or None, away_table_size=match.away.size or None, stage=match.stage or None),
                         missing_inputs=[k for k, v in parts.items() if v is None],
                         assumptions=[f"Missing components use {weight['missing']}"], effective_weights=dict(weight["weights"]),
                         calibration_id=revision("outlook", weight)),
            links=dict(match_page=https(match.link), table_league_id=match.league if builder.STANDINGS.get(match.league) else None,
                       destination_ids=destination_ids)))
    catalog = envelope("catalog", leagues=leagues, teams=sorted(teams.values(), key=lambda x:x["id"]),
                       networks=networks, service_plans=plans, assets=sorted(assets.values(), key=lambda x:x["id"]),
                       service_plan_migrations=[])
    # A conservative common horizon from successfully collected calendar scopes.
    # Gaps deliberately yield null rather than declaring unobserved days empty.
    horizon = None
    date_sets = [set(s["covered_dates"]) for s in scopes]
    common = sorted(set.intersection(*date_sets))
    if common and all((datetime.fromisoformat(b)-datetime.fromisoformat(a)).days == 1 for a,b in zip(common,common[1:])) and len({s["date_zone"] for s in scopes}) == 1:
        zone = ZoneInfo(scopes[0]["date_zone"])
        horizon = dict(start=instant(datetime.fromisoformat(common[0]).replace(tzinfo=zone)),
                       end_exclusive=instant((datetime.fromisoformat(common[-1])+timedelta(days=1)).replace(tzinfo=zone)))
    health = report.get("broadcast_health", report.get("broadcasts", {}))
    sources = health.get("sources", health) if isinstance(health, dict) else {}
    broadcast_sources = []
    for name, raw in sources.items():
        if not isinstance(raw, dict):
            continue
        status = raw.get("status", raw.get("diagnostics", {}).get("status", "unknown"))
        status = {"complete":"ok", "outside_requested_horizon":"unknown"}.get(status,status)
        if status not in ("ok","partial","unavailable","unsupported","unknown"):
            status="unknown"
        broadcast_sources.append(dict(source=source_id(name), state=status, attempted_at=instant(built_at),
            validated_at=instant(built_at) if raw.get("successful", 0) else None,
            observed_at=None, diagnostics=[str(raw.get("diagnostics", {}).get("reason", "Coverage retained in original source report"))][:100]))
    schedule = envelope("schedule", catalog_revision=catalog["content_revision"], policy_id=POLICY_ID,
        fixture_sources_complete=True, source_scopes=scopes, confirmed_horizon=horizon,
        broadcast_sources=broadcast_sources, fixtures=fixtures, team_vitals=vitals,
        evidence=list({e["id"]:e for e in evidence}.values()),
        destinations=list({d["id"]:d for d in destinations}.values()))
    return dict(catalog=catalog, schedule=schedule, policy=policy, editorial=None)


def add_editorial(artifacts, *, automatic_path=None, shared_path=None, published_at):
    """Convert optional public AI, preserving original identities and lifetimes.

    Unbound legacy automatic entries cannot be rebound using today's schedule.
    A failed optional input contributes a diagnostic, never blocks required facts.
    """
    from companion_contract import decode, read_bytes, validate, validate_references

    result = deepcopy(artifacts)
    now = parsed(published_at)
    fixtures = {f["source_refs"][0]["id"]: f for f in result["schedule"]["fixtures"]}
    diagnostics = []

    def bindings(values):
        if not isinstance(values, list):
            raise ValueError("missing original fixture bindings")
        bound = {}
        for value in values:
            mid = str(value["id"])
            kickoff = value["kickoff_utc"] if "kickoff_utc" in value else value["kickoff"]
            kickoff = instant(kickoff) if kickoff else None
            if mid in bound:
                raise ValueError("duplicate original fixture binding")
            bound[mid] = (value["league_id"], kickoff)
        return bound

    def references(ids, bound, league):
        refs = []
        for mid in ids:
            match = fixtures.get(str(mid))
            if (not match or bound.get(str(mid)) != (league, match["timing"]["kickoff"]) or
                    match["league_id"] != league):
                raise ValueError("missing, moved or rescheduled editorial fixture")
            refs.append(dict(fixture_id=match["id"], kickoff=bound[str(mid)][1]))
        if len({r["fixture_id"] for r in refs}) != len(refs):
            raise ValueError("duplicate editorial reference")
        return refs

    def sources(values):
        values = values or []
        if not isinstance(values, list):
            raise ValueError("invalid editorial sources")
        if any(not isinstance(v, dict) for v in values):
            raise ValueError("invalid editorial source object")
        return [dict(title=v["title"], url=v["url"]) for v in values if https(v.get("url"))]

    def item(key, league, refs, expiry, *, kind="rating", text="", headline=None,
             interest=None, total=None, components=None, weights=None, links=None, period=None, provenance=None):
        return dict(id=key, kind=kind, league_id=league, fixture_refs=refs,
                    headline=headline, text=text, interest=interest, sources=sources(links),
                    expires_at=instant(expiry), event_period=period, rating_total=total,
                    automatic_components=components, automatic_weights=weights,
                    provenance=provenance)

    def convert_shared(value):
        if value.get("version") != 1:
            raise ValueError("unsupported shared envelope")
        original = datetime.fromtimestamp(value["created_at"] / 1000, timezone.utc)
        expiry = original + timedelta(days=1)
        if not original <= now < expiry:
            raise ValueError("shared input outside original lifetime")
        response, bound = value["response"], bindings(value["fixtures"])
        if response.get("version") not in (1, 2):
            raise ValueError("unsupported shared response")
        matches, headlines = [], []
        for entry in response["matches"]:
            mid = str(entry["id"])
            league = bound[mid][0]
            refs = references([mid], bound, league)
            matches.append(item(revision("shared-rating", [response["request_id"], mid]), league,
                refs, expiry, text=entry.get("blurb", ""), total=entry["score"], links=entry.get("sources")))
        for index, entry in enumerate(response.get("headlines", [])):
            league = entry["league_id"]
            end = parsed(entry["expires_at"])
            if not original < end <= expiry:
                raise ValueError("shared headline extends original lifetime")
            if end <= now:
                continue
            period = None
            if entry.get("event_start") and entry.get("event_end"):
                period = dict(start=instant(entry["event_start"]), end_exclusive=instant(entry["event_end"]))
            refs = references(entry["match_ids"], bound, league)
            headlines.append(item(revision("shared-headline", [response["request_id"], index]), league,
                refs, end, kind="headline", text=entry.get("lede", ""), headline=entry["headline"],
                interest=entry["interest"], links=entry.get("sources"), period=period))
        overview = response.get("overview")
        if overview and overview.get("match_ids"):
            leagues = {bound[str(mid)][0] for mid in overview["match_ids"]}
            if len(leagues) == 1:
                league = next(iter(leagues))
                headlines.append(item(revision("shared-overview", response["request_id"]), league,
                    references(overview["match_ids"], bound, league), expiry, kind="match_story",
                    text=overview["text"], links=overview.get("sources")))
            else:
                diagnostics.append("shared legacy overview omitted: references span leagues")
        return dict(origin="shared", response_id=response["request_id"], request_id=response["request_id"],
            generated_at=None, prompted_at=instant(original), published_at=None,
            expires_at=instant(expiry), focus_until=None, model=None, effort=None, aggregation_id=None,
            influence_weight=value["weight"], headlines=headlines, matches=matches)

    def convert_automatic(value):
        generated = parsed(value["generated_at"])
        expiry = min(generated + timedelta(hours=story_state.MAX_AGE_HOURS), parsed(value["focus_until"]))
        if not generated <= now < expiry:
            raise ValueError("automatic input outside original lifetime")
        matches, headlines, omitted = [], [], 0

        def origin(entry):
            original = entry["companion_origin"]
            created = parsed(original["generated_at"])
            end = min(expiry, parsed(original["expires_at"]))
            if not created <= now < end or end > created + timedelta(hours=story_state.MAX_AGE_HOURS):
                raise ValueError("automatic entry outside original lifetime")
            return bindings(original["fixture_bindings"]), end

        def provenance(entry, key="companion_origin"):
            value = entry[key]
            return {name: instant(value[name]) for name in ("generated_at", "prompted_at", "expires_at") if value.get(name)}

        for mid, entry in value.get("rankings", {}).items():
            try:
                bound, end = origin(entry)
                league = bound[mid][0]
                refs = references([mid], bound, league)
                text, links = "", []
                if entry.get("blurb"):
                    try:
                        blurb_origin = entry.get("companion_blurb_origin", entry.get("companion_origin"))
                        blurb_bound = bindings(blurb_origin["fixture_bindings"])
                        references([mid], blurb_bound, league)
                        blurb_end = min(end, parsed(blurb_origin["expires_at"]))
                        blurb_created = parsed(blurb_origin["generated_at"])
                        if not blurb_created <= now < blurb_end or blurb_end > blurb_created + timedelta(hours=story_state.MAX_AGE_HOURS):
                            raise ValueError("invalid blurb lifetime")
                        end, text, links = blurb_end, entry["blurb"], entry.get("sources")
                    except (ValueError, KeyError, TypeError):
                        diagnostics.append("automatic blurb omitted: missing/invalid original binding or lifetime")
                if end <= now:
                    raise ValueError("expired automatic entry")
                components = {k: entry[k] for k in story_state.COMPONENTS}
                matches.append(item(revision("automatic-rating", [entry["companion_origin"], mid]), league,
                    refs, end, text=text, total=entry["score"], components=components,
                    weights=value["rating_weights"], links=links, provenance=provenance(entry)))
            except (ValueError, KeyError, TypeError):
                omitted += 1
        for index, entry in enumerate(value.get("league_blurbs", [])):
            try:
                bound, end = origin(entry)
                ids = entry.get("match_ids", [])
                league = entry.get("league_id") or entry.get("league")
                if league is None and ids:
                    league = bound[str(ids[0])][0]
                refs = references(ids, bound, league)
                period = None
                if entry.get("event_start") and entry.get("event_end"):
                    period = dict(start=instant(entry["event_start"]), end_exclusive=instant(entry["event_end"]))
                if entry.get("expires_at"):
                    end = min(end, parsed(entry["expires_at"]))
                if end <= now:
                    raise ValueError("expired automatic headline")
                headlines.append(item(revision("automatic-headline", [entry["companion_origin"], index]), league,
                    refs, end, kind="headline", text=entry.get("text", ""), headline=entry["headline"],
                    interest=entry.get("interest"), links=entry.get("sources"), period=period,
                    provenance=provenance(entry)))
            except (ValueError, KeyError, TypeError):
                omitted += 1
        # General news has no authored per-league headline. Preserve it as a
        # match story only when its full original reference set is one league.
        news = list(value.get("lede_items", [])) + list((value.get("forecast") or {}).get("items", []))
        news += [dict(entry, match_ids=[mid], text=entry.get("note", ""))
                 for mid, entry in value.get("notes", {}).items()]
        if isinstance(value.get("overview"), dict):
            news.append(value["overview"])
        for index, entry in enumerate(news):
            try:
                bound, end = origin(entry)
                ids = entry.get("match_ids", list(bound))
                leagues = {bound[str(mid)][0] for mid in ids}
                if len(leagues) != 1:
                    raise ValueError("general news has no unique league")
                league = next(iter(leagues))
                refs = references(ids, bound, league)
                headlines.append(item(revision("automatic-news", [entry["companion_origin"], index]), league,
                    refs, end, kind="match_story", text=entry.get("text", ""),
                    links=entry.get("sources"), provenance=provenance(entry)))
            except (ValueError, KeyError, TypeError):
                omitted += 1
        if value.get("headline"):
            diagnostics.append("automatic page-wide headline retained by website only; companion uses bound league headlines and match stories")
        if omitted:
            diagnostics.append(f"automatic: {omitted} entries omitted for missing/invalid original binding or lifetime")
        if omitted and not matches and not headlines:
            return None
        return dict(origin="automatic", response_id=revision("automatic", [value["generated_at"], value.get("model")]),
            request_id=None, generated_at=instant(generated), prompted_at=None,
            published_at=instant(value.get("updated_at") or generated), expires_at=instant(expiry),
            focus_until=instant(value["focus_until"]), model=value.get("model"), effort=value.get("effort"),
            aggregation_id=value.get("score_calibration"), influence_weight=None, headlines=headlines, matches=matches)

    slots = dict(automatic=None, shared=None)
    for key, path, convert in (("automatic", automatic_path, convert_automatic), ("shared", shared_path, convert_shared)):
        if path is None or not Path(path).exists():
            continue
        try:
            raw = read_bytes(Path(path), 8 * 1024 * 1024 if key == "automatic" else 512 * 1024)
            value = decode(raw)
            if not isinstance(value, dict):
                raise ValueError("optional editorial must be an object")
            slots[key] = convert(value)
            probe = envelope("editorial", catalog_revision=result["catalog"]["content_revision"],
                             policy_id=result["policy"]["policy_id"], **slots)
            validate("editorial", probe)
            validate_references(dict(result, editorial=probe))
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError) as exc:
            slots[key] = None
            diagnostics.append(f"{key}: omitted invalid optional input ({type(exc).__name__})")
    result["editorial"] = envelope("editorial", catalog_revision=result["catalog"]["content_revision"],
        policy_id=result["policy"]["policy_id"], diagnostics=diagnostics, **slots) if diagnostics or any(slots.values()) else None
    return result
