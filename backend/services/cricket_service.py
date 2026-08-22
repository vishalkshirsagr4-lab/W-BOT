import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.config.settings import get_settings
from backend.services import http_client

logger = logging.getLogger(__name__)

CRICKET_API_URL = "https://api.cricapi.com/v1/currentMatches"
CACHE_TTL_SECONDS = 60
SERVICE_ERROR = "🏏 Cricket service is temporarily unavailable.\n\nPlease try again in a little while."
NO_LIVE_MATCHES = "🏏 No live cricket matches right now.\n\nTry:\n• /cricket\n• /score india"

_COMPLETED_STATUS = re.compile(r"\b(won|lost|draw|tie|abandoned|cancelled|canceled|no result|result)\b", re.I)
_LIVE_STATUS = re.compile(r"\b(live|in progress|day\s+\d+|session|innings? break|stumps)\b", re.I)
_UPCOMING_STATUS = re.compile(r"\b(schedule|scheduled|upcoming|starts?|yet to begin|not started)\b", re.I)


def _team_flag(name: str) -> str:
    flags = {
        "india": "🇮🇳", "pakistan": "🇵🇰", "australia": "🇦🇺", "england": "🏴",
        "new zealand": "🇳🇿", "south africa": "🇿🇦", "sri lanka": "🇱🇰",
        "bangladesh": "🇧🇩", "west indies": "🏏",
    }
    lowered = name.lower()
    return next((flag for team, flag in flags.items() if team in lowered), "🏏")


def is_live_match(match: dict[str, Any]) -> bool:
    """Conservatively identify an active match from CricketData's mixed feed."""
    status = str(match.get("status") or "").strip()
    if not status or _COMPLETED_STATUS.search(status) or _UPCOMING_STATUS.search(status):
        return False
    if _LIVE_STATUS.search(status):
        return True
    scores = match.get("score")
    return isinstance(scores, list) and any(isinstance(item, dict) and item.get("r") is not None for item in scores)


def _inning_team(inning: str) -> str:
    return re.sub(r"\s+Inning\s+\d+.*$", "", inning, flags=re.I).strip() or "Team"


def format_score_line(team: str, score: dict[str, Any]) -> str:
    runs = score.get("r", "?")
    wickets = score.get("w", "?")
    overs = score.get("o")
    overs_text = f" ({overs} ov)" if overs is not None else ""
    return f"{_team_flag(team)} {team}: {runs}/{wickets}{overs_text}"


def match_matches_team(match: dict[str, Any], query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    teams = match.get("teams") if isinstance(match.get("teams"), list) else []
    haystack = [str(item) for item in teams]
    haystack.append(str(match.get("name") or ""))
    return any(needle in item.lower() for item in haystack)


def _format_match(match: dict[str, Any], index: int | None = None) -> str:
    teams = match.get("teams") if isinstance(match.get("teams"), list) else []
    team_names = [str(team) for team in teams if team]
    title = " vs ".join(team_names) if team_names else str(match.get("name") or "Unknown match")
    prefix = f"{index}\ufe0f\u20e3 " if index is not None else ""
    lines = [f"{prefix}{title}"]
    scores = match.get("score") if isinstance(match.get("score"), list) else []
    for score in scores:
        if isinstance(score, dict):
            lines.append(format_score_line(_inning_team(str(score.get("inning") or "")), score))
    if match.get("venue"):
        lines.append(f"📍 {match['venue']}")
    if match.get("status"):
        lines.append(f"🔴 {match['status']}")
    return "\n".join(lines)


class CricketService:
    def __init__(self, api_key: str | None = None, cache_ttl: int = CACHE_TTL_SECONDS) -> None:
        self.api_key = api_key
        self.cache_ttl = cache_ttl
        self._cached_matches: list[dict[str, Any]] | None = None
        self._cached_at = 0.0
        self._cache_lock = asyncio.Lock()
        self.api_calls_today = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self._last_fetch_failed = False

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def fetch_matches(self) -> list[dict[str, Any]]:
        if not self.is_configured():
            logger.warning("cricket feature disabled: CRICKET_API_KEY not configured")
            return []
        async with self._cache_lock:
            if self._cached_matches is not None and time.monotonic() - self._cached_at < self.cache_ttl:
                self.cache_hits += 1
                logger.info("cricket_service: cache_hit=true")
                return list(self._cached_matches)
            self.cache_misses += 1
            try:
                response = await http_client.get_shared_http_client().get(
                    CRICKET_API_URL,
                    params={"apikey": self.api_key, "offset": 0},
                    timeout=10.0,
                )
                if response.status_code == 429:
                    logger.warning("cricket_service: CricketData API returned 429; rate limit may be reached")
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get("status") != "success" or not isinstance(body.get("data"), list):
                    raise ValueError("invalid CricketData response")
                matches = [item for item in body["data"] if isinstance(item, dict)]
                self._cached_matches = matches
                self._cached_at = time.monotonic()
                self._last_fetch_failed = False
                self.api_calls_today += 1
                logger.info("cricket_service: fetched live cricket data live_matches=%d", sum(is_live_match(item) for item in matches))
                return list(matches)
            except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError) as exc:
                self._last_fetch_failed = True
                logger.warning("cricket_service: CricketData request failed: %s", type(exc).__name__)
                return []
            except Exception:
                self._last_fetch_failed = True
                logger.exception("cricket_service: CricketData request failed")
                return []

    async def live_matches(self, team: str | None = None) -> list[dict[str, Any]]:
        matches = [match for match in await self.fetch_matches() if is_live_match(match)]
        return [match for match in matches if not team or match_matches_team(match, team)]

    async def format_response(self, team: str | None = None) -> str:
        matches = await self.live_matches(team)
        if not matches:
            if self._last_fetch_failed:
                return SERVICE_ERROR
            return NO_LIVE_MATCHES if not team else f"🏏 No live cricket match found for {team}."
        heading = "🏏 LIVE CRICKET" + (f"\n\nMatches for {team.title()}" if team else "")
        body = "\n\n".join(_format_match(match, index if len(matches) > 1 else None) for index, match in enumerate(matches, 1))
        return f"{heading}\n\n{body}"


_default_service: CricketService | None = None


def get_cricket_service() -> CricketService:
    global _default_service
    if _default_service is None:
        _default_service = CricketService(get_settings().CRICKET_API_KEY)
    return _default_service


def is_cricket_request(text: str) -> bool:
    normalized = text.lower().strip()
    if normalized.startswith(("/cricket", "/live", "/score")):
        return True
    return not normalized.startswith("/") and bool(re.search(r"\b(live\s+cricket|live\s+score|cricket\s+score|cricket|\w+\s+(?:ka\s+)?(?:live\s+)?score|\w+\s+match)\b", normalized))


async def _set_subscription(db: Any, payload: dict[str, Any], team: str | None) -> None:
    await db["cricket_subscriptions"].update_one(
        {"chat_id": payload.get("chat_id"), "feature": "cricket"},
        {"$set": {"user_id": payload.get("platform_id") or payload.get("phone_number"), "enabled": True, "team": team, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def _disable_subscription(db: Any, payload: dict[str, Any]) -> None:
    await db["cricket_subscriptions"].update_one(
        {"chat_id": payload.get("chat_id"), "feature": "cricket"},
        {"$set": {"enabled": False, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def handle_cricket_request(text: str, db: Any = None, payload: dict[str, Any] | None = None) -> str:
    service = get_cricket_service()
    words = text.strip().split()
    command = words[0].lower() if words and words[0].startswith("/") else ""
    argument = " ".join(words[1:]).strip() if command else ""
    if command == "/cricket" and (argument.lower() in {"subscribe", "alert"} or argument.lower().startswith(("subscribe ", "alert "))):
        if db is None:
            return "🏏 Cricket alerts require the database to be configured."
        team = argument.split(maxsplit=1)[1].strip() if " " in argument else None
        await _set_subscription(db, payload or {}, team)
        suffix = f" for {team.title()}" if team else ""
        return f"🏏 Cricket alerts enabled{suffix}!\n\nI'll notify you when the tracked live score changes."
    if command == "/cricket" and argument.lower() == "unsubscribe":
        if db is not None:
            await _disable_subscription(db, payload or {})
        return "🏏 Cricket alerts disabled."
    if command == "/cricket" and argument.lower() == "status":
        state = "configured" if service.is_configured() else "disabled"
        cache = "active" if service._cached_matches is not None else "empty"
        return f"🏏 Cricket API\n\nAPI status: {state}\nCache: {cache}\nAPI calls today: {service.api_calls_today}\nCache hits: {service.cache_hits}"
    return await service.format_response(team=argument if command == "/score" and argument else None)