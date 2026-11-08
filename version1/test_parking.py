import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from postgrest.exceptions import APIError
from supabase import ClientOptions, create_client
from supabase_auth.errors import AuthApiError

from app import app
from parking_data import preview_spaces


class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.old_config = {key: app.config.get(key) for key in
                           ("TESTING", "SECRET_KEY", "PARKING_PREVIEW_DATABASE")}
        self.addCleanup(lambda: app.config.update(self.old_config))
        app.config.update(TESTING=True, SECRET_KEY="parking-tests-only",
                          PARKING_PREVIEW_DATABASE=Path(self.directory.name) / "preview.sqlite3")
        self.browser = app.test_client()
        self.client = Mock()
        self.client.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id="user-1"))
        self.factory = patch("app.supabase_client", return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.rows = [{"id": 1, "label": "A1", "occupied": False,
                      "hourly_rate": "5.50", "map_x": 1, "map_y": 1}]
        self.query = self.client.rpc.return_value
        self.query.execute.return_value = SimpleNamespace(data=self.rows)

    def login(self):
        with self.browser.session_transaction() as current:
            current.update(access_token="access", refresh_token="refresh", csrf_token="original-csrf")

    def test_anonymous_requests_do_not_reach_supabase(self):
        result = self.browser.get("/api/parking-spaces")
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.json["code"], "auth_required")
        self.assertEqual(self.client.mock_calls, [])

    def test_authenticated_data_is_normalized_and_not_cached(self):
        self.login()
        result = self.browser.get("/api/parking-spaces")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["source"], "supabase")
        self.assertEqual(result.json["spaces"][0]["hourly_rate"], 5.5)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.client.auth.get_user.assert_called_once_with("access")
        self.client.postgrest.auth.assert_called_once_with("access")

    def test_expired_auth_refreshes_query_token_and_preserves_csrf(self):
        self.login()
        self.client.auth.get_user.side_effect = [
            AuthApiError("Expired", 401, "bad_jwt"),
            SimpleNamespace(user=SimpleNamespace(id="user-1")),
        ]
        self.client.auth.refresh_session.return_value = SimpleNamespace(
            session=SimpleNamespace(access_token="new-access", refresh_token="new-refresh"))
        self.assertEqual(self.browser.get("/api/parking-spaces").status_code, 200)
        self.client.postgrest.auth.assert_called_once_with("new-access")
        with self.browser.session_transaction() as current:
            self.assertEqual(current["csrf_token"], "original-csrf")

    def test_invalid_auth_never_reads_the_table(self):
        self.login()
        self.client.auth.get_user.return_value = SimpleNamespace(user=None)
        self.assertEqual(self.browser.get("/api/parking-spaces").status_code, 401)
        self.client.rpc.assert_not_called()
        with self.browser.session_transaction() as current:
            self.assertNotIn("access_token", current)

    def test_outage_is_reported_without_clearing_session(self):
        self.login()
        self.query.execute.side_effect = httpx.ConnectError("private connection details")
        result = self.browser.get("/api/parking-spaces")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn(b"private connection details", result.data)
        with self.browser.session_transaction() as current:
            self.assertEqual(current["access_token"], "access")

    def test_missing_table_is_reported_without_demo_fallback(self):
        self.login()
        self.query.execute.side_effect = APIError({"code": "PGRST205", "message": "missing"})
        result = self.browser.get("/api/parking-spaces")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json["code"], "not_ready")
        self.assertNotIn("spaces", result.json)
        self.assertFalse(Path(app.config["PARKING_PREVIEW_DATABASE"]).exists())

    def test_empty_table_is_a_successful_empty_result(self):
        self.login()
        self.query.execute.return_value = SimpleNamespace(data=[])
        result = self.browser.get("/api/parking-spaces")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["spaces"], [])

    def test_invalid_database_rows_are_rejected(self):
        self.login()
        for field, value in [("occupied", "false"), ("hourly_rate", "NaN"),
                             ("map_x", 7), ("label", ""), ("id", True)]:
            with self.subTest(field=field):
                self.query.execute.return_value = SimpleNamespace(data=[{**self.rows[0], field: value}])
                result = self.browser.get("/api/parking-spaces")
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json["code"], "invalid_data")
        self.query.execute.return_value = SimpleNamespace(data=self.rows * 2)
        self.assertEqual(self.browser.get("/api/parking-spaces").status_code, 503)

    def test_preview_reads_real_local_changes_without_supabase(self):
        result = self.browser.get("/api/preview/parking-spaces")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["source"], "local_demo")
        self.assertEqual(len(result.json["spaces"]), 12)
        with closing(sqlite3.connect(app.config["PARKING_PREVIEW_DATABASE"])) as connection:
            with connection:
                connection.execute("update parking_spaces set occupied=1, hourly_rate=7.5 where label='A3'")
        result = self.browser.get("/api/preview/parking-spaces")
        space = next(row for row in result.json["spaces"] if row["label"] == "A3")
        self.assertTrue(space["occupied"])
        self.assertEqual(space["hourly_rate"], 7.5)
        self.assertEqual(self.client.mock_calls, [])

    def test_preview_does_not_reseed_an_existing_empty_table(self):
        preview_spaces(app.config["PARKING_PREVIEW_DATABASE"])
        with closing(sqlite3.connect(app.config["PARKING_PREVIEW_DATABASE"])) as connection:
            with connection:
                connection.execute("delete from parking_spaces")
        self.assertEqual(self.browser.get("/api/preview/parking-spaces").json["spaces"], [])

    def test_parking_endpoints_reject_writes(self):
        self.browser.get("/")
        with self.browser.session_transaction() as current:
            token = current["csrf_token"]
        for path in ("/api/parking-spaces", "/api/preview/parking-spaces"):
            self.assertEqual(self.browser.post(path, data={"csrf_token": token}).status_code, 405)
        self.assertEqual(self.client.mock_calls, [])

    def test_real_sdk_sends_verified_user_token_to_postgrest(self):
        self.login()
        requests = []

        def handle(request):
            requests.append(request)
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": "00000000-0000-4000-8000-000000000001",
                    "aud": "authenticated", "app_metadata": {}, "user_metadata": {},
                    "created_at": "2026-11-08T00:00:00Z"})
            if request.url.path == "/rest/v1/rpc/smart_parking_map":
                return httpx.Response(200, json=self.rows)
            raise AssertionError("Unexpected SDK request")

        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = create_client("https://example.supabase.co", "public-test-key",
                options=ClientOptions(httpx_client=http, persist_session=False, auto_refresh_token=False))
            with patch("app.supabase_client", return_value=client):
                result = self.browser.get("/api/parking-spaces")
        self.assertEqual(result.status_code, 200)
        self.assertEqual([str(r.url.path) for r in requests], ["/auth/v1/user", "/rest/v1/rpc/smart_parking_map"])
        self.assertEqual([r.method for r in requests], ["GET", "POST"])
        self.assertTrue(all(r.headers["authorization"] == "Bearer access" for r in requests))


if __name__ == "__main__":
    unittest.main()
