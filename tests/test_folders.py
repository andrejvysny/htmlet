"""Folder API and share-link integration tests."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


class FolderTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.object(settings, "data_dir", Path(root).resolve()))
        stack.enter_context(patch.object(settings, "api_keys", {"a" * 32}))
        self.client = stack.enter_context(TestClient(app))
        self.key = {"X-API-Key": "a" * 32}

    def test_auth_and_validation(self) -> None:
        client = self.client
        self.assertEqual(client.get("/api/folders").status_code, 401)
        self.assertEqual(
            client.post("/api/folders", json={"slug": "team"}).status_code, 401
        )
        self.assertEqual(
            client.post(
                "/api/folders", headers=self.key, json={"slug": "team"}
            ).status_code,
            201,
        )
        self.assertEqual(
            client.post(
                "/api/folders", headers=self.key, json={"slug": "team"}
            ).status_code,
            409,
        )
        self.assertEqual(
            client.post(
                "/api/folders", headers=self.key, json={"slug": "../bad"}
            ).status_code,
            400,
        )
        self.assertEqual(
            client.put("/api/folders/team/pages/missing", headers=self.key).status_code,
            404,
        )

    def test_lifecycle_and_public_picker(self) -> None:
        client = self.client
        for folder in ("team", "other"):
            self.assertEqual(
                client.post(
                    "/api/folders", headers=self.key, json={"slug": folder}
                ).status_code,
                201,
            )
        for page in ("report", "notes"):
            self.assertEqual(
                client.put(
                    f"/api/files/{page}", headers=self.key, content=b"<h1>Hi</h1>"
                ).status_code,
                200,
            )
            self.assertEqual(
                client.put(
                    f"/api/folders/team/pages/{page}", headers=self.key
                ).status_code,
                200,
            )
        picker = client.get("/f/team")
        self.assertEqual(picker.status_code, 200)
        self.assertIn('href="/report"', picker.text)
        self.assertIn('href="/notes"', picker.text)
        self.assertIn("Find a page", picker.text)
        self.assertIn("folderForm", client.get("/").text)
        self.assertEqual(client.get("/static/index.js").status_code, 200)

        self.assertEqual(
            client.put("/api/folders/other/pages/report", headers=self.key).status_code,
            200,
        )
        self.assertNotIn('href="/report"', client.get("/f/team").text)
        self.assertIn('href="/report"', client.get("/f/other").text)
        self.assertEqual(
            client.delete("/api/files/report", headers=self.key).status_code, 200
        )
        self.assertEqual(
            client.get("/api/folders", headers=self.key).json()["folders"][0]["pages"],
            [],
        )
        self.assertEqual(
            client.delete("/api/folders/team", headers=self.key).status_code, 200
        )
        self.assertEqual(client.get("/f/team").status_code, 404)
        self.assertEqual(client.get("/notes").status_code, 200)


if __name__ == "__main__":
    unittest.main()
