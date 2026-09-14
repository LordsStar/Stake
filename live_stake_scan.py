#!/usr/bin/env python3
"""Escáner directo y efímero de Stake Sports Data API.

No importa módulos Blindado, no lee snapshot.json y no escribe estado persistente.
La salida live_stake_scan.json se destina exclusivamente a un artefacto de Actions.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://odds-data.stake.com"
RD = ZoneInfo("America/Santo_Domingo")
UTC = timezone.utc
OUT = Path("live_stake_scan.json")
VIRTUAL_SPORTS = {
    "ecricket", "efootball-bots", "etouchdown", "fifa", "madden", "nba2k",
    "dota-2-duels", "counter-strike-2-duels",
}
SIM_RE = re.compile(r"\b(simulat(?:ed|ion)|virtual|bots?|duels?|sr[lm]|ebasket|efootball|ecricket)\b", re.I)
PRIMARY_MARKET_RE = re.compile(
    r"^(moneyline|match winner|winner|full time result|1x2|draw no bet|double chance|"
    r"game lines?|match result|to win match|head to head|h2h)$", re.I
)


def slug(value: Any) -> str:
    return quote(str(value or "").strip(), safe="-")


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000.0
        try:
            return datetime.fromtimestamp(stamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value).strip()
    if raw.isdigit():
        stamp = float(raw)
        if stamp > 10_000_000_000:
            stamp /= 1000.0
        try:
            return datetime.fromtimestamp(stamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def odds_value(value: Any) -> float | None:
    try:
        odd = float(value)
        return odd if odd > 1 else None
    except (TypeError, ValueError):
        return None


class StakeDirect:
    def __init__(self, workers: int = 8):
        self.workers = max(1, min(workers, 10))
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "stake-live-scan/1.0",
        })
        api_key = os.getenv("STAKE_ODDS_API_KEY", "").strip()
        if api_key:
            self.session.headers["X-API-KEY"] = api_key
        self.errors: list[str] = []

    def get(self, path: str) -> Any:
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=25)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f"{path}: {exc}") from exc
                time.sleep(0.7 * (2 ** attempt))
        raise RuntimeError(path)

    def parallel(self, jobs: list[tuple[str, Any]]) -> list[tuple[Any, Any]]:
        found: list[tuple[Any, Any]] = []
        if not jobs:
            return found
        with ThreadPoolExecutor(max_workers=min(self.workers, len(jobs))) as pool:
            futures = {pool.submit(self.get, path): context for path, context in jobs}
            for future in as_completed(futures):
                context = futures[future]
                try:
                    found.append((context, future.result()))
                except Exception as exc:
                    self.errors.append(f"{context}: {exc}")
        return found


def window(kind: str, now_rd: datetime) -> tuple[datetime, datetime]:
    start = now_rd
    if kind == "afternoon":
        start = max(now_rd, datetime.combine(now_rd.date(), dt_time(12, 0), RD))
        end = datetime.combine(now_rd.date(), dt_time(18, 0), RD)
    elif kind == "auto":
        if now_rd.hour < 12:
            end = datetime.combine(now_rd.date(), dt_time(12, 0), RD)
        elif now_rd.hour < 16:
            end = datetime.combine(now_rd.date(), dt_time(18, 0), RD)
        else:
            # Desde las 4:00 p. m. RD incluye la cartelera nocturna,
            # especialmente MLB, sin recuperar eventos ya iniciados.
            end = datetime.combine(now_rd.date(), dt_time(23, 59, 59), RD)
    elif kind == "next_6_hours":
        end = now_rd + timedelta(hours=6)
    elif kind == "next_24_hours":
        end = now_rd + timedelta(hours=24)
    elif kind == "morning":
        end = datetime.combine(now_rd.date(), dt_time(12, 0), RD)
        if end <= start:
            end = start
    else:
        end = datetime.combine(now_rd.date(), dt_time(23, 59, 59), RD)
    return start, end


def flatten_markets(groups: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        for bundle in group.get("markets") or []:
            for market in bundle if isinstance(bundle, list) else [bundle]:
                if not isinstance(market, dict):
                    continue
                marker = str(market.get("id") or (market.get("name"), market.get("specifiers", "")))
                if marker not in seen:
                    seen.add(marker)
                    result.append(market)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--window",
        choices=("auto", "afternoon", "remaining_today", "next_6_hours", "next_24_hours", "morning"),
        default="auto",
    )
    parser.add_argument("--min-odds", type=float, default=1.40)
    parser.add_argument("--max-odds", type=float, default=2.00)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    now_rd = datetime.now(RD)
    start_rd, end_rd = window(args.window, now_rd)
    api = StakeDirect(args.workers)
    audit = {
        "sports_discovered": 0, "sports_scanned": 0, "categories": 0,
        "tournaments": 0, "fixtures_listed": 0, "fixtures_in_window": 0,
        "fixtures_detailed": 0, "fixtures_with_candidates": 0,
        "excluded_simulations": 0, "excluded_started_live_disabled": 0,
    }

    sports_payload = api.get("/sports")
    sports = [
        row for row in (sports_payload if isinstance(sports_payload, list) else [])
        if isinstance(row, dict) and row.get("slug") and row.get("enabled", True)
    ]
    audit["sports_discovered"] = len(sports)
    candidates: list[dict[str, Any]] = []

    for sport_row in sports:
        sport = str(sport_row["slug"])
        audit["sports_scanned"] += 1
        if sport in VIRTUAL_SPORTS or SIM_RE.search(str(sport_row.get("name", sport))):
            audit["excluded_simulations"] += 1
            continue

        try:
            category_payload = api.get(f"/sports/{slug(sport)}/categories")
        except Exception as exc:
            api.errors.append(str(exc))
            continue
        categories = [
            row for row in (category_payload.get("categories", []) if isinstance(category_payload, dict) else [])
            if isinstance(row, dict) and row.get("slug") and row.get("enabled", True)
        ]
        audit["categories"] += len(categories)

        tournament_jobs = [
            (f"/sports/{slug(sport)}/{slug(category['slug'])}/tournaments", category)
            for category in categories
        ]
        tournament_contexts: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for category, payload in api.parallel(tournament_jobs):
            tournaments = payload.get("tournaments", []) if isinstance(payload, dict) else []
            for tournament in tournaments:
                if isinstance(tournament, dict) and tournament.get("slug") and tournament.get("enabled", True):
                    tournament_contexts.append((category, tournament))
        audit["tournaments"] += len(tournament_contexts)

        fixture_jobs = [
            (
                f"/sports/{slug(sport)}/{slug(category['slug'])}/{slug(tournament['slug'])}/fixtures",
                (category, tournament),
            )
            for category, tournament in tournament_contexts
        ]
        fixtures: dict[str, dict[str, Any]] = {}
        for context, payload in api.parallel(fixture_jobs):
            category, tournament = context
            for fixture in payload.get("fixtures", []) if isinstance(payload, dict) else []:
                if isinstance(fixture, dict) and fixture.get("slug"):
                    item = dict(fixture)
                    item["_category"] = category
                    item["_tournament"] = tournament
                    fixtures[str(item.get("id") or item["slug"])] = item
        audit["fixtures_listed"] += len(fixtures)

        eligible: list[dict[str, Any]] = []
        for fixture in fixtures.values():
            start_utc = parse_dt(fixture.get("startTime") or fixture.get("date"))
            status = str(fixture.get("status") or "").lower()
            text = " ".join(str(fixture.get(k, "")) for k in ("name", "slug", "type"))
            disabled = (
                not fixture.get("enabled", True)
                or fixture.get("blacklisted", False)
                or str(fixture.get("type") or "match").lower() != "match"
                or not fixture.get("preMatchEnabled", True)
            )
            if SIM_RE.search(text):
                audit["excluded_simulations"] += 1
            elif disabled or status in {"live", "ended", "inactive", "inplay", "in-play"} or not start_utc:
                audit["excluded_started_live_disabled"] += 1
            elif start_rd <= start_utc.astimezone(RD) < end_rd:
                eligible.append(fixture)
        audit["fixtures_in_window"] += len(eligible)

        detail_jobs = [(f"/fixtures/{fixture['slug']}", fixture) for fixture in eligible]
        for fixture, detail in api.parallel(detail_jobs):
            audit["fixtures_detailed"] += 1
            node = detail.get("fixture", {}) if isinstance(detail, dict) else {}
            competitors = fixture.get("competitors") or []
            names = [str(c.get("name", "")) for c in competitors if isinstance(c, dict)]
            if len(names) < 2:
                names = re.split(r"\s+-\s+", str(node.get("name") or fixture.get("name", "")), maxsplit=1)
            event_text = " ".join(names + [str(node.get("name", "")), sport])
            if len(names) < 2 or SIM_RE.search(event_text):
                audit["excluded_simulations"] += 1
                continue

            event_candidates: list[dict[str, Any]] = []
            for market in flatten_markets(detail.get("groups", [])):
                market_name = str(market.get("name", "")).strip()
                if (
                    str(market.get("status", "active")).lower() not in {"active", "open"}
                    or not PRIMARY_MARKET_RE.match(market_name)
                ):
                    continue
                active_outcomes = []
                for outcome in market.get("outcomes") or []:
                    if not isinstance(outcome, dict) or not outcome.get("active", True):
                        continue
                    odd = odds_value(outcome.get("odds"))
                    if odd:
                        active_outcomes.append((outcome, odd))
                if len(active_outcomes) < 2:
                    continue
                for outcome, odd in active_outcomes:
                    if args.min_odds <= odd <= args.max_odds:
                        event_candidates.append({
                            "market_id": market.get("id"),
                            "market": market_name,
                            "selection": str(outcome.get("name", "")),
                            "odds": odd,
                            "point": outcome.get("point"),
                            "market_updated_at": market.get("updatedAt"),
                        })
            if event_candidates:
                audit["fixtures_with_candidates"] += 1
                category = fixture.get("_category", {})
                tournament = fixture.get("_tournament", {})
                candidates.append({
                    "event_id": node.get("id") or fixture.get("id"),
                    "fixture_slug": fixture.get("slug"),
                    "sport": sport,
                    "category": category.get("name") or category.get("slug"),
                    "tournament": tournament.get("name") or tournament.get("slug"),
                    "home": names[0],
                    "away": names[1],
                    "start_time_utc": (parse_dt(node.get("startTime") or fixture.get("startTime")) or datetime.now(UTC)).isoformat(),
                    "start_time_rd": (parse_dt(node.get("startTime") or fixture.get("startTime")) or datetime.now(UTC)).astimezone(RD).isoformat(),
                    "markets": event_candidates,
                })

    candidates.sort(key=lambda row: row["start_time_utc"])
    result = {
        "schema": 1,
        "source": "direct:https://odds-data.stake.com",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "generated_at_rd": datetime.now(RD).isoformat(),
        "window": args.window,
        "window_start_rd": start_rd.isoformat(),
        "window_end_rd": end_rd.isoformat(),
        "odds_range": [args.min_odds, args.max_odds],
        "uses_snapshot": False,
        "uses_blindado": False,
        "audit": audit,
        "errors": api.errors,
        "candidates": candidates,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit": audit, "errors": len(api.errors), "candidates": len(candidates)}, ensure_ascii=False))
    print("LIVE_SCAN_RESULT=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    if audit["sports_discovered"] == 0:
        raise RuntimeError("La API no devolvió deportes; la corrida no es válida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
