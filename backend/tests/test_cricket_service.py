import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.services.cricket_service import (
    CricketService,
    format_score_line,
    is_live_match,
    match_matches_team,
)


class CricketServiceTests(unittest.TestCase):
    def test_live_match_detection_accepts_live_status(self) -> None:
        match = {"status": "Day 3: 2nd Session - Gloucestershire trail by 87 runs", "score": [{"inning": "Gloucestershire Inning 1", "r": 30, "w": 0, "o": 11}]}
        self.assertTrue(is_live_match(match))

    def test_completed_match_is_not_treated_as_live(self) -> None:
        match = {"status": "India won by 6 wickets", "score": [{"inning": "India Inning 2", "r": 180, "w": 4, "o": 32.2}]}
        self.assertFalse(is_live_match(match))

    def test_match_search_uses_partial_caseinsensitive_team_name(self) -> None:
        matches = [{"name": "India vs Australia", "teams": ["India", "Australia"]}]
        self.assertTrue(match_matches_team(matches[0], "india"))
        self.assertFalse(match_matches_team(matches[0], "england"))

    def test_score_formatting_keeps_overs_as_supplied(self) -> None:
        score = {"inning": "India Inning 1", "r": 142, "w": 3, "o": 17.2}
        self.assertEqual(format_score_line("India", score), "🇮🇳 India: 142/3 (17.2 ov)")

    def test_api_key_missing_disables_service(self) -> None:
        service = CricketService(api_key=None)
        self.assertFalse(service.is_configured())

    def test_cache_avoids_duplicate_fetches(self) -> None:
        captured = {"calls": 0}

        class DummyResponse:
            def __init__(self) -> None:
                self.status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"status": "success", "data": [{"id": "m1", "status": "Live", "teams": ["India", "Australia"]}]}

        async def fake_get(*args, **kwargs):
            captured["calls"] += 1
            return DummyResponse()

        with patch("backend.services.http_client.get_shared_http_client") as shared_client:
            shared_client.return_value = SimpleNamespace(get=fake_get)
            service = CricketService(api_key="abc")
            first = asyncio.run(service.fetch_matches())
            second = asyncio.run(service.fetch_matches())
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(captured["calls"], 1)

    def test_failure_response_is_handled_gracefully(self) -> None:
        service = CricketService(api_key="abc")

        class DummyResponse:
            def __init__(self) -> None:
                self.status_code = 429

            def raise_for_status(self) -> None:
                raise Exception("429")

        async def fake_get(*args, **kwargs):
            return DummyResponse()

        with patch("backend.services.http_client.get_shared_http_client") as shared_client:
            shared_client.return_value = SimpleNamespace(get=fake_get)
            result = asyncio.run(service.fetch_matches())
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
