import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from postgrest.exceptions import APIError
from supabase import ClientOptions, create_client

from app import app
from admin_parking import failed_attempts


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.settings = {key: app.config.get(key) for key in
                         ("TESTING", "SECRET_KEY", "ADMIN_CODE", "PARKING_ADMIN_SECRET")}
        self.addCleanup(lambda: app.config.update(self.settings))
        app.config.update(TESTING=True, SECRET_KEY="admin-tests-only", ADMIN_CODE="1234",
                          PARKING_ADMIN_SECRET="server-test-key")
        failed_attempts.clear()
        self.browser = app.test_client()
        self.client = Mock()
        self.rows = [{"id": 1, "label": "A1", "occupied": False,
                      "hourly_rate": 5, "map_x": 1, "map_y": 1,
                      "reserved": True, "reserved_until": "2026-11-08T14:00:00+00:00"}]
        self.schedule = {"spaces": self.rows, "reservations": [], "schedule_date": "2026-11-08",
                         "server_time": "2026-11-08T10:00:00+00:00", "timezone": "Europe/Bucharest"}
        self.client.rpc.return_value.execute.return_value = SimpleNamespace(data=self.schedule)
        self.factory = patch("admin_parking.admin_client", return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def form(self, path="/admin", **values):
        self.browser.get("/admin")
        with self.browser.session_transaction() as current:
            token = current["csrf_token"]
        return self.browser.post(path, data={"csrf_token": token, **values})

    def login(self):
        return self.form(code="1234")

    def test_code_is_required_for_page_and_all_data_actions(self):
        page = self.browser.get("/admin")
        self.assertIn(b"Admin access", page.data)
        self.assertNotIn(b'id="parking-lot"', page.data)
        self.assertEqual(self.browser.get("/api/admin/parking-spaces").status_code, 401)
        self.assertEqual(self.form("/api/admin/parking-spaces/1", action="occupy").status_code, 401)
        self.client.rpc.assert_not_called()

    def test_correct_code_opens_dashboard_without_user_login(self):
        self.assertEqual(self.login().location, "/admin")
        page = self.browser.get("/admin")
        self.assertIn(b'Manage your parking.', page.data)
        self.assertIn(b'data-parking-url="/api/admin/parking-spaces"', page.data)
        self.assertNotIn(b"server-test-key", page.data)
        self.assertNotIn(b"1234", page.data)
        with self.browser.session_transaction() as current:
            self.assertGreater(current["admin_expires_at"], time.time())
            self.assertNotIn("access_token", current)

    def test_wrong_and_unicode_codes_do_not_unlock(self):
        for code in ("", "123", "9999", "árvíz"):
            self.assertEqual(self.form(code=code).status_code, 401)
        with self.browser.session_transaction() as current:
            self.assertNotIn("admin_expires_at", current)

    def test_repeated_wrong_codes_are_throttled_even_with_new_cookie(self):
        for _ in range(5):
            self.assertEqual(self.form(code="wrong").status_code, 401)
        self.browser = app.test_client()
        self.assertEqual(self.form(code="1234").status_code, 429)

    def test_csrf_is_required_to_login_or_change_data(self):
        self.assertEqual(self.browser.post("/admin", data={"code": "1234"}).status_code, 400)
        self.login()
        self.assertEqual(self.browser.post("/api/admin/parking-spaces/1", data={"action": "free"}).status_code, 400)
        self.client.rpc.assert_not_called()

    def test_expired_admin_session_cannot_write(self):
        self.login()
        with self.browser.session_transaction() as current:
            current["admin_expires_at"] = time.time() - 1
        self.assertEqual(self.form("/api/admin/parking-spaces/1", action="occupy").status_code, 401)
        self.client.rpc.assert_not_called()

    def test_admin_reads_reserved_state_from_supabase_function(self):
        self.login()
        result = self.browser.get("/api/admin/parking-spaces")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["source"], "supabase")
        self.assertTrue(result.json["spaces"][0]["reserved"])
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.client.rpc.assert_called_once_with("smart_parking_admin_schedule", {"p_server_key": "server-test-key"})

    def test_future_reservations_are_listed_without_marking_the_space_reserved_now(self):
        self.login()
        self.rows[0].update(reserved=False, reserved_until=None)
        self.schedule["reservations"] = [{"id": "00000000-0000-4000-8000-000000000001",
            "parking_space_id": 1, "start_time": "2026-11-08T15:00:00+00:00", "end_time": "2026-11-08T17:00:00+00:00"}]
        result = self.browser.get("/api/admin/parking-spaces")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json["spaces"][0]["reserved"])
        self.assertEqual(len(result.json["spaces"][0]["reservations"]), 1)
        self.assertEqual(result.json["schedule_date"], "2026-11-08")

    def test_actions_send_only_validated_parameters(self):
        self.login()
        booking_id = "00000000-0000-4000-8000-000000000001"
        for action in ("occupy", "free", "reserve", "move", "cancel"):
            result = self.form("/api/admin/parking-spaces/1", action=action,
                               date="2026-11-08", start_time="17:00", end_time="24:00",
                               source_id="2", booking_id=booking_id, user_id="forged", role="admin")
            self.assertEqual(result.status_code, 200)
            self.client.rpc.assert_called_with("smart_parking_admin_action", {
                "p_action": action, "p_space_id": 1, "p_source_id": 2 if action == "move" else None,
                "p_day": "2026-11-08" if action == "reserve" else None,
                "p_start": "17:00" if action == "reserve" else None,
                "p_end": "24:00" if action == "reserve" else None,
                "p_booking_id": booking_id if action == "cancel" else None,
                "p_server_key": "server-test-key",
            })

    def test_invalid_actions_never_reach_database(self):
        self.login()
        for values in ({"action": "delete"}, {"action": "reserve"},
                       {"action": "reserve", "date": "bad", "start_time": "17:00", "end_time": "18:00"},
                       {"action": "reserve", "date": "2026-11-08", "start_time": "18:00", "end_time": "17:00"},
                       {"action": "reserve", "date": "2026-11-08", "start_time": "24:00", "end_time": "24:00"},
                       {"action": "reserve", "date": "2026-11-08", "start_time": "17:00", "end_time": "24:01"},
                       {"action": "cancel", "booking_id": "not-a-uuid"},
                       {"action": "move", "source_id": "1"}, {"action": "move"},
                       {"action": "move", "source_id": "-3"}):
            self.assertEqual(self.form("/api/admin/parking-spaces/1", **values).status_code, 400)
        self.client.rpc.assert_not_called()

    def test_conflicts_do_not_expose_database_details(self):
        self.login()
        self.client.rpc.return_value.execute.side_effect = APIError({"code": "PT409", "message": "private detail"})
        result = self.form("/api/admin/parking-spaces/1", action="reserve", date="2026-11-08",
                           start_time="17:00", end_time="18:00")
        self.assertEqual(result.status_code, 409)
        self.assertNotIn(b"private detail", result.data)

    def test_missing_configuration_has_no_demo_fallback(self):
        self.login()
        self.factory.stop()
        app.config["PARKING_ADMIN_SECRET"] = ""
        result = self.browser.get("/api/admin/parking-spaces")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json["code"], "setup_required")
        self.assertNotIn("spaces", result.json)

    def test_lock_admin_removes_write_access_and_preserves_user_login(self):
        self.login()
        with self.browser.session_transaction() as current:
            current.update(access_token="existing-user", refresh_token="existing-refresh")
        self.assertEqual(self.form("/admin/logout").location, "/admin")
        self.assertEqual(self.browser.get("/api/admin/parking-spaces").status_code, 401)
        with self.browser.session_transaction() as current:
            self.assertEqual(current["access_token"], "existing-user")

    def test_real_sdk_sends_admin_write_to_supabase(self):
        self.login()
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = create_client("https://example.supabase.co", "server-test-key",
                options=ClientOptions(httpx_client=http, persist_session=False, auto_refresh_token=False))
            with patch("admin_parking.admin_client", return_value=client):
                result = self.form("/api/admin/parking-spaces/1", action="move", source_id="2")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/rest/v1/rpc/smart_parking_admin_action")
        self.assertEqual(json.loads(requests[0].content)["p_server_key"], "server-test-key")
        self.assertEqual(json.loads(requests[0].content)["p_source_id"], 2)


if __name__ == "__main__":
    unittest.main()
