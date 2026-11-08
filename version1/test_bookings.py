import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from postgrest.exceptions import APIError
from supabase import ClientOptions, create_client
from supabase_auth.errors import AuthApiError

from app import app


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.old_config = {key: app.config.get(key) for key in ("TESTING", "SECRET_KEY")}
        self.addCleanup(lambda: app.config.update(self.old_config))
        app.config.update(TESTING=True, SECRET_KEY="booking-tests-only")
        self.browser = app.test_client()
        self.client = Mock()
        self.client.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id="signed-in-user"))
        self.factory = patch("app.supabase_client", return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.identifier = "00000000-0000-4000-8000-000000000001"
        self.data = {"spaces": [{"id": 1, "label": "A1", "occupied": False,
            "hourly_rate": 5, "map_x": 1, "map_y": 1, "reserved": False, "reserved_until": None}],
            "intervals": [{"parking_space_id": 1, "start_time": "2026-11-08T15:00:00Z", "end_time": "2026-11-08T16:00:00Z"}],
            "bookings": [{"id": self.identifier, "parking_space_id": 1,
                "start_time": "2026-11-08T15:00:00Z", "end_time": "2026-11-08T16:00:00Z",
                "actual_start": None, "actual_end": None, "status": "confirmed",
                "hourly_rate": "5.00", "estimated_total": "5.00", "user_id": "never-send-this-field"}],
            "schedule_date": "2026-11-08", "server_time": "2026-11-08T10:00:00Z", "timezone": "Europe/Bucharest"}
        self.client.rpc.return_value.execute.return_value = SimpleNamespace(data=self.data)

    def login(self):
        with self.browser.session_transaction() as current:
            current.update(access_token="access", refresh_token="refresh", csrf_token="original-csrf")

    def post(self, path="/api/bookings", **values):
        with self.browser.session_transaction() as current:
            current.setdefault("csrf_token", "csrf-test")
            token = current["csrf_token"]
        return self.browser.post(path, data={"csrf_token": token, **values})

    def test_anonymous_users_cannot_open_pages_or_read_or_create_bookings(self):
        for path in ("/booking", "/active-parking"):
            self.assertEqual(self.browser.get(path).location, "/")
        self.assertEqual(self.browser.get("/api/bookings").status_code, 401)
        self.assertEqual(self.post(space_id="1").status_code, 401)
        self.client.assert_not_called()

    def test_admin_pin_session_does_not_replace_a_user_login(self):
        with self.browser.session_transaction() as current:
            current["admin_expires_at"] = 9999999999
        self.assertEqual(self.browser.get("/api/bookings").status_code, 401)
        self.client.assert_not_called()

    def test_both_pages_render_shared_navigation_and_real_controls(self):
        self.login()
        booking = self.browser.get("/booking?space=1")
        active = self.browser.get("/active-parking")
        self.assertEqual(booking.status_code, 200)
        self.assertEqual(active.status_code, 200)
        self.assertIn(b'id="booking-form"', booking.data)
        self.assertIn(b'data-selected-space="1"', booking.data)
        self.assertIn(b'id="current-parking"', active.data)
        self.assertIn(b'href="/active-parking"', booking.data)
        self.assertNotIn(b'data-placeholder="Booking"', booking.data)
        self.assertNotIn(b'filename="parking.js"', active.data)

    def test_overview_normalizes_data_and_omits_ownership_fields(self):
        self.login()
        response = self.browser.get("/api/bookings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["bookings"][0]["estimated_total"], 5)
        self.assertEqual(response.json["spaces"][0]["intervals"][0]["end_time"], "2026-11-08T16:00:00Z")
        self.assertNotIn(b"never-send-this-field", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.client.postgrest.auth.assert_called_with("access")
        self.client.rpc.assert_called_once_with("smart_parking_user_schedule", {})

    def test_reserving_uses_verified_user_token_and_ignores_forged_owner_and_price(self):
        self.login()
        self.client.rpc.return_value.execute.return_value = SimpleNamespace(data={"booking_id": self.identifier})
        response = self.post(space_id="1", date="2026-11-08", start_time="17:00", end_time="24:00",
                             user_id="another-person", role="admin", hourly_rate="0")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["booking_id"], self.identifier)
        self.client.rpc.assert_called_once_with("smart_parking_user_action", {
            "p_action": "reserve", "p_space_id": 1, "p_day": "2026-11-08", "p_start": "17:00", "p_end": "24:00"})
        self.client.postgrest.auth.assert_called_with("access")

    def test_invalid_fields_do_not_reach_the_booking_function(self):
        self.login()
        valid = {"space_id": "1", "date": "2026-11-08", "start_time": "17:00", "end_time": "18:00"}
        for field, value in (("space_id", "0"), ("date", "bad"), ("start_time", "24:00"),
                             ("end_time", "24:01"), ("end_time", "16:00"), ("start_time", "<script>")):
            with self.subTest(field=field, value=value):
                self.assertEqual(self.post(**{**valid, field: value}).status_code, 400)
        self.client.rpc.assert_not_called()

    def test_csrf_is_required_for_every_write(self):
        self.login()
        for path in ("/api/bookings", "/api/bookings/" + self.identifier + "/cancel"):
            self.assertEqual(self.browser.post(path, data={}).status_code, 400)
        self.client.rpc.assert_not_called()

    def test_actions_only_send_booking_id_and_action_to_supabase(self):
        self.login()
        for action in ("start", "finish", "cancel"):
            response = self.post("/api/bookings/" + self.identifier + "/" + action, user_id="forged-owner")
            self.assertEqual(response.status_code, 200)
            self.client.rpc.assert_called_with("smart_parking_user_action", {
                "p_action": action, "p_booking_id": self.identifier})

    def test_unknown_actions_and_invalid_ids_do_not_reach_database(self):
        self.login()
        self.assertEqual(self.post("/api/bookings/" + self.identifier + "/delete").status_code, 400)
        self.assertEqual(self.post("/api/bookings/not-a-uuid/start").status_code, 404)
        self.client.rpc.assert_not_called()

    def test_other_users_booking_errors_are_generic(self):
        self.login()
        self.client.rpc.return_value.execute.side_effect = APIError({"code": "PT404", "message": "private owner details"})
        response = self.post("/api/bookings/" + self.identifier + "/cancel")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"private owner details", response.data)

    def test_auth_refresh_is_used_for_new_pages_and_preserves_csrf(self):
        self.login()
        self.client.auth.get_user.side_effect = [AuthApiError("Expired", 401, "bad_jwt"),
            SimpleNamespace(user=SimpleNamespace(id="signed-in-user"))]
        self.client.auth.refresh_session.return_value = SimpleNamespace(
            session=SimpleNamespace(access_token="new-access", refresh_token="new-refresh"))
        self.assertEqual(self.browser.get("/api/bookings").status_code, 200)
        self.client.postgrest.auth.assert_called_with("new-access")
        with self.browser.session_transaction() as current:
            self.assertEqual(current["csrf_token"], "original-csrf")

    def test_invalid_user_loses_access(self):
        self.login()
        self.client.auth.get_user.return_value = SimpleNamespace(user=None)
        self.assertEqual(self.browser.get("/api/bookings").status_code, 401)
        self.client.rpc.assert_not_called()

    def test_outage_does_not_clear_an_existing_login(self):
        self.login()
        self.client.rpc.return_value.execute.side_effect = httpx.ConnectError("private connection data")
        response = self.browser.get("/api/bookings")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"private connection data", response.data)
        with self.browser.session_transaction() as current:
            self.assertEqual(current["access_token"], "access")

    def test_invalid_database_values_are_rejected(self):
        self.login()
        for field, value in (("estimated_total", "NaN"), ("status", "unknown"), ("start_time", "bad")):
            data = deepcopy(self.data)
            data["bookings"][0][field] = value
            self.client.rpc.return_value.execute.return_value = SimpleNamespace(data=data)
            self.assertEqual(self.browser.get("/api/bookings").status_code, 503)

    def test_real_sdk_passes_user_jwt_to_the_read_function(self):
        self.login()
        requests = []

        def handle(request):
            requests.append(request)
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json={"id": self.identifier, "aud": "authenticated",
                    "app_metadata": {}, "user_metadata": {}, "created_at": "2026-11-08T00:00:00Z"})
            if request.url.path == "/rest/v1/rpc/smart_parking_user_schedule":
                return httpx.Response(200, json=self.data)
            raise AssertionError("Unexpected request")

        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            client = create_client("https://example.supabase.co", "public-test-key",
                options=ClientOptions(httpx_client=http, persist_session=False, auto_refresh_token=False))
            with patch("app.supabase_client", return_value=client):
                self.assertEqual(self.browser.get("/api/bookings").status_code, 200)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.headers["authorization"] == "Bearer access" for request in requests))


if __name__ == "__main__":
    unittest.main()
