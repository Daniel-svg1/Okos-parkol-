"""A belepes fontos esetei, valo felhasznalok letrehozasa nelkul."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from supabase_auth.errors import AuthApiError, AuthRetryableError

from app import app


class AuthTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY="only-used-in-tests")
        self.browser = app.test_client()
        self.client = Mock()
        self.mock_client = patch("app.supabase_client", return_value=self.client)
        self.mock_client.start()
        self.addCleanup(self.mock_client.stop)

    def form(self, path="/", **values):
        self.browser.get("/register" if path == "/register" else "/")
        with self.browser.session_transaction() as current:
            token = current["csrf_token"]
        return self.browser.post(path, data={"csrf_token": token, **values})

    def logged_in(self):
        with self.browser.session_transaction() as current:
            current.update(access_token="access", refresh_token="refresh")

    def test_unauthenticated_cannot_open_inside(self):
        result = self.browser.get("/inside")
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.location, "/")
        self.client.auth.get_user.assert_not_called()

    def test_csrf_required(self):
        result = self.browser.post("/", data={"email": "person@example.com", "password": "x"})
        self.assertEqual(result.status_code, 400)
        self.client.auth.sign_in_with_password.assert_not_called()

    def test_unicode_csrf_is_rejected_without_server_error(self):
        self.browser.get("/")
        result = self.browser.post("/", data={"csrf_token": "árvíztűrő"})
        self.assertEqual(result.status_code, 400)

    def test_signup_waits_for_confirmation(self):
        self.client.auth.sign_up.return_value = SimpleNamespace(session=None)
        result = self.form("/register", name="Daniel", email="person@example.com", password="strong-example")
        self.assertEqual(result.location, "/")
        self.assertIn(b"Check your email", self.browser.get("/").data)
        with self.browser.session_transaction() as current:
            self.assertNotIn("access_token", current)
            self.assertNotIn("password", current)
        payload = self.client.auth.sign_up.call_args.args[0]
        self.assertEqual(payload["options"]["data"], {"full_name": "Daniel"})

    def test_invalid_registration_never_reaches_supabase(self):
        for values in (
            {"name": "", "email": "person@example.com", "password": "strong-example"},
            {"name": "Daniel", "email": "bad-email", "password": "strong-example"},
            {"name": "Daniel", "email": "person@example.com", "password": "short"},
        ):
            self.assertEqual(self.form("/register", **values).status_code, 400)
        self.client.auth.sign_up.assert_not_called()

    def test_valid_login_then_online_user_check(self):
        self.client.auth.sign_in_with_password.return_value = SimpleNamespace(
            session=SimpleNamespace(access_token="access", refresh_token="refresh")
        )
        self.client.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id="user-1"))
        result = self.form(email="person@example.com", password="correct-password")
        self.assertEqual(result.location, "/inside")
        result = self.browser.get("/inside")
        self.assertIn(b"You are in", result.data)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.client.auth.get_user.assert_called_once_with("access")
        with self.browser.session_transaction() as current:
            self.assertNotIn("password", current)
        self.assertIn("HttpOnly", result.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", result.headers["Set-Cookie"])

    def test_wrong_password_has_readable_error(self):
        self.client.auth.sign_in_with_password.side_effect = AuthApiError("Invalid credentials", 400, "invalid_credentials")
        result = self.form(email="person@example.com", password="wrong-password")
        self.assertEqual(result.status_code, 400)
        self.assertNotIn(b"You are in", result.data)
        self.assertIn(b"Check your details", result.data)

    def test_login_without_session_does_not_grant_access(self):
        self.client.auth.sign_in_with_password.return_value = SimpleNamespace(session=None)
        self.assertEqual(self.form(email="person@example.com", password="some-password").status_code, 400)
        self.assertEqual(self.browser.get("/inside").location, "/")

    def test_unconfirmed_email_cannot_enter(self):
        self.client.auth.sign_in_with_password.side_effect = AuthApiError("Unconfirmed", 400, "email_not_confirmed")
        result = self.form(email="person@example.com", password="correct-password")
        self.assertIn(b"Confirm your email", result.data)
        self.assertEqual(self.browser.get("/inside").status_code, 302)

    def test_expired_token_refreshes_and_rechecks_user(self):
        self.logged_in()
        self.client.auth.get_user.side_effect = [
            AuthApiError("Expired", 401, "bad_jwt"),
            SimpleNamespace(user=SimpleNamespace(id="user-1")),
        ]
        self.client.auth.refresh_session.return_value = SimpleNamespace(
            session=SimpleNamespace(access_token="new-access", refresh_token="new-refresh")
        )
        self.assertIn(b"You are in", self.browser.get("/inside").data)
        self.client.auth.get_user.assert_called_with("new-access")

    def test_invalid_session_is_removed(self):
        self.logged_in()
        self.client.auth.get_user.side_effect = AuthApiError("Invalid", 401, "bad_jwt")
        self.client.auth.refresh_session.side_effect = AuthApiError("Invalid", 400, "refresh_token_not_found")
        self.assertEqual(self.browser.get("/inside").location, "/")
        with self.browser.session_transaction() as current:
            self.assertNotIn("access_token", current)

    def test_network_error_does_not_grant_access(self):
        self.logged_in()
        self.client.auth.get_user.side_effect = httpx.ConnectError("offline")
        result = self.browser.get("/inside")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn(b"You are in", result.data)

    def test_supabase_outage_keeps_session_for_retry(self):
        self.logged_in()
        self.client.auth.get_user.side_effect = AuthRetryableError("Service unavailable", 503)
        self.assertEqual(self.browser.get("/inside").status_code, 503)
        with self.browser.session_transaction() as current:
            self.assertEqual(current["access_token"], "access")

    def test_resend_requires_email_but_not_password(self):
        result = self.form("/resend-confirmation", email="person@example.com")
        self.assertEqual(result.location, "/")
        payload = self.client.auth.resend.call_args.args[0]
        self.assertEqual(payload["email"], "person@example.com")
        self.assertEqual(payload["type"], "signup")
        self.assertNotIn("password", payload)

    def test_confirmation_redirect_alone_does_not_log_in(self):
        self.browser.get("/auth/confirmed")
        self.assertEqual(self.browser.get("/inside").location, "/")

    def test_signout_clears_session(self):
        self.logged_in()
        result = self.form("/logout")
        self.assertEqual(result.location, "/")
        self.client.auth.sign_out.assert_called_once_with({"scope": "local"})
        self.assertEqual(self.browser.get("/inside").location, "/")


if __name__ == "__main__":
    unittest.main()
