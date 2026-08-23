import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.commands import handle_nezuko_command


class Collection:
    def __init__(self, count=0):
        self.count = count
        self.documents = []

    async def count_documents(self, query):
        return self.count

    async def insert_one(self, document):
        self.documents.append(document)
        return SimpleNamespace(inserted_id="test")

    async def update_one(self, *args, **kwargs):
        return SimpleNamespace()


class Database:
    def __init__(self):
        self.collections = {
            "users": Collection(3),
            "notes": Collection(4),
            "conversations": Collection(2),
            "admin_actions": Collection(),
            "announcements": Collection(),
            "settings": Collection(),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


class AdminCommandTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.payload = {
            "phone_number": "918660108587",
            "platform_id": "918660108587@s.whatsapp.net",
            "chat_id": "918660108587@s.whatsapp.net",
        }

    def run_command(self, text, payload=None):
        return asyncio.run(handle_nezuko_command(self.db, payload or self.payload, text))

    def test_authorized_admin_status(self):
        result = self.run_command("Nezuko status")
        self.assertIn("Admin status", result["reply"])

    def test_unauthorized_admin_is_rejected(self):
        payload = {**self.payload, "phone_number": "900000000000", "platform_id": "900000000000@s.whatsapp.net"}
        result = self.run_command("/admin status", payload)
        self.assertIn("Only an authorized admin", result["reply"])

    def test_admin_logs_and_broadcast_are_recorded(self):
        result = self.run_command("Nezuko broadcast campus update")
        self.assertIn("queued", result["reply"])
        self.assertEqual(len(self.db["announcements"].documents), 1)

    def test_statistics_uses_database_counts(self):
        result = self.run_command("/admin statistics")
        self.assertIn("Users: 3", result["reply"])
        self.assertIn("Notes: 4", result["reply"])


if __name__ == "__main__":
    unittest.main()