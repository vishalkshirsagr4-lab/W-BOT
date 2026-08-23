import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.ai.chat import build_runtime_system_prompt
from backend.routes.whatsapp import _handle_slash_command, WhatsAppMessagePayload
from backend.services.ai_router import AIRouter
from backend.services.gemini_service import GeminiService
from backend.services.openrouter_service import OpenRouterService
import backend.services.http_client as http_client

from backend.routes.whatsapp import receive_whatsapp_message
from backend.services.commands import extract_command
from backend.services.gemini_service import GeminiService
from backend.services.nezuko import sanitize_text, should_trigger_nezuko, build_help_text, is_authorized_admin


class NezukoServiceTests(unittest.TestCase):
    def test_sanitize_text_removes_control_chars(self) -> None:
        self.assertEqual(sanitize_text("  Hello\nWorld\x00  "), "Hello World")

    def test_should_trigger_nezuko_is_case_insensitive(self) -> None:
        self.assertTrue(should_trigger_nezuko("Hello Nezuko how are you?"))
        self.assertTrue(should_trigger_nezuko("hello nezuko"))
        self.assertFalse(should_trigger_nezuko("hello there friend"))

    def test_help_text_contains_core_commands(self) -> None:
        help_text = build_help_text()
        self.assertIn("help", help_text.lower())
        self.assertIn("summary", help_text.lower())
        self.assertIn("admin", help_text.lower())

    def test_extract_command_strips_wake_word(self) -> None:
        self.assertEqual(extract_command("Nezuko status"), "status")
        self.assertEqual(extract_command("hello nezuko broadcast hi"), "broadcast hi")
        self.assertEqual(extract_command("please help me"), "please help me")

    def test_owner_number_is_treated_as_admin(self) -> None:
        self.assertTrue(is_authorized_admin("918861591838"))

    def test_formatted_phone_numbers_are_treated_as_admin(self) -> None:
        self.assertTrue(is_authorized_admin("+91 8861 591838"))

    def test_gemini_service_uses_ai_api_key_fallback(self) -> None:
        with patch.dict(os.environ, {"AI_API_KEY": "shared-key"}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            service = GeminiService()
            self.assertEqual(service.api_key, "shared-key")

    def test_gemini_service_uses_keyword_sdk_call(self) -> None:
        service = GeminiService(api_key="test-key")

        class DummyModels:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def generate_content(self, *, model: str, contents: list[object], config: object) -> dict[str, object]:
                self.calls.append({"model": model, "contents": contents, "config": config})
                return {"text": "ok"}

        class DummyClient:
            def __init__(self) -> None:
                self.models = DummyModels()

        client = DummyClient()
        response = service._invoke_generate_content(client, "test-model", ["hello"], object())
        self.assertEqual(response, {"text": "ok"})
        self.assertEqual(client.models.calls[0]["model"], "test-model")

    def test_openrouter_service_uses_configured_key(self) -> None:
        with patch("backend.services.openrouter_service.get_settings", return_value=SimpleNamespace(OPENROUTER_API_KEY="or-key", AI_API_KEY=None)):
            service = OpenRouterService()
            self.assertEqual(service.api_key, "or-key")

    def test_shared_http_client_falls_back_when_http2_is_unavailable(self) -> None:
        asyncio.run(http_client.close_shared_http_client())

        class DummyClient:
            async def aclose(self) -> None:
                return None

        def fake_client(*args: object, **kwargs: object) -> object:
            if kwargs.get("http2"):
                raise RuntimeError("h2 support unavailable")
            return DummyClient()

        with patch("backend.services.http_client.httpx.AsyncClient", side_effect=fake_client):
            client = http_client.get_shared_http_client()

        self.assertIsInstance(client, DummyClient)
        asyncio.run(http_client.close_shared_http_client())

    def test_ai_router_does_not_retry_quota_errors(self) -> None:
        router = AIRouter()
        self.assertFalse(router._is_retryable_failure(RuntimeError("HTTP 429: rate limit exceeded")))
        self.assertFalse(router._is_retryable_failure(RuntimeError("RESOURCE_EXHAUSTED")))
        self.assertTrue(router._is_retryable_failure(RuntimeError("HTTP 503: temporary server error")))

    def test_build_runtime_system_prompt_renders_current_context(self) -> None:
        prompt = build_runtime_system_prompt()
        self.assertIn("Current Date:", prompt)
        self.assertNotIn("{{CURRENT_DATE}}", prompt)
        self.assertNotIn("{{CURRENT_DAY}}", prompt)
        self.assertNotIn("{{CURRENT_TIME}}", prompt)
        self.assertIn("Timezone: Asia/Kolkata", prompt)

    def test_user_lookup_uses_phone_or_platform_id_for_profile_commands(self) -> None:
        class DummyUsers:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def find_one(self, query: dict[str, object], *args: object, **kwargs: object) -> dict[str, object] | None:
                self.calls.append(query)
                if query == {"platform_id": "user-42"}:
                    return {"platform_id": "user-42", "phone": "+919999999999", "sender_name": "Asha"}
                return None

        db = SimpleNamespace(users=DummyUsers())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        payload = WhatsAppMessagePayload(
            platform_id="user-42",
            phone_number="",
            chat_id="chat-42",
            message="/user",
            timestamp=123,
        )

        result = asyncio.run(_handle_slash_command(request, db, payload, "/user"))

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        self.assertIn("Asha", result["reply"])

    def test_admin_stats_command_accepts_configured_admin_phone(self) -> None:
        class DummyUsers:
            async def find_one(self, query: dict[str, object], *args: object, **kwargs: object) -> dict[str, object] | None:
                return None

            async def count_documents(self, *args: object, **kwargs: object) -> int:
                return 3

        db = SimpleNamespace(users=DummyUsers())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        payload = WhatsAppMessagePayload(
            platform_id="admin-1",
            phone_number="918660108587",
            chat_id="chat-admin",
            message="/admin stats",
            timestamp=123,
        )

        result = asyncio.run(_handle_slash_command(request, db, payload, "/admin stats"))

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        self.assertIn("Admin stats", result["reply"])

    def test_register_command_persists_whatsapp_user_profile(self) -> None:
        class DummyCollection:
            def __init__(self) -> None:
                self.saved: dict[str, object] | None = None

            async def find_one(self, query: dict[str, object], *args: object, **kwargs: object) -> dict[str, object] | None:
                return None

            async def insert_one(self, doc: dict[str, object]) -> object:
                self.saved = doc
                return SimpleNamespace(inserted_id="abc123")

        db = SimpleNamespace(users=DummyCollection())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        payload = WhatsAppMessagePayload(
            platform_id="wa-123",
            phone_number="919999999999",
            chat_id="chat-whatsapp",
            sender_name="Asha",
            message="/register Asha",
            timestamp=123,
        )

        result = asyncio.run(_handle_slash_command(request, db, payload, "/register Asha"))

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        self.assertIn("Registration complete", result["reply"])
        self.assertEqual(db.users.saved["platform_id"], "wa-123")
        self.assertEqual(db.users.saved["phone"], "919999999999")

    def test_receive_whatsapp_message_uses_decision_result(self) -> None:
        class DummyUsers:
            async def update_one(self, *args: object, **kwargs: object) -> None:
                return None

        db = SimpleNamespace(
            users=DummyUsers(),
            groups=SimpleNamespace(update_one=AsyncMock()),
            blocked_users=SimpleNamespace(find_one=AsyncMock(return_value=None)),
            blocked_groups=SimpleNamespace(find_one=AsyncMock(return_value=None)),
            chat_settings=SimpleNamespace(find_one=AsyncMock(return_value={"reply_mode": "Always", "ai_on": True})),
        )
        payload = WhatsAppMessagePayload(
            platform_id="whatsapp",
            phone_number="919999999999",
            chat_id="chat-1",
            message="nezuko hello",
            timestamp=123,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(http_client=None, weather_api_key="")))

        with patch("backend.routes.whatsapp.decide", new=AsyncMock(return_value={"allowed": True, "ai_enabled": True, "reason": "trigger", "trigger_detected": True, "reply_mode": "Always"})), patch("backend.routes.whatsapp.handle_nezuko_command", new=AsyncMock(return_value={"status": "success", "reply": "hi there"})):
            result = asyncio.run(receive_whatsapp_message(request, payload, db))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["reply"], "hi there")


if __name__ == "__main__":
    unittest.main()
