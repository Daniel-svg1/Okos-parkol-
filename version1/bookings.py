import math
import re
from datetime import date, datetime
from uuid import UUID

import httpx
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from postgrest.exceptions import APIError
from supabase_auth.errors import AuthError, AuthRetryableError

from admin_parking import normalize_parking_map


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Invalid time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Missing timezone")
    return parsed


def booking_overview(data):
    spaces = normalize_parking_map(data["spaces"])
    date.fromisoformat(data["schedule_date"])
    timestamp(data["server_time"])
    if data["timezone"] != "Europe/Bucharest":
        raise ValueError("Invalid timezone")
    by_id = {space["id"]: space for space in spaces}
    for space in spaces:
        space["intervals"] = []
    for interval in data["intervals"]:
        start, end = timestamp(interval["start_time"]), timestamp(interval["end_time"])
        if end <= start:
            raise ValueError("Invalid interval")
        by_id[interval["parking_space_id"]]["intervals"].append({
            "start_time": interval["start_time"], "end_time": interval["end_time"],
        })
    bookings = []
    for row in data["bookings"]:
        UUID(row["id"])
        if type(row["parking_space_id"]) is not int or row["parking_space_id"] not in by_id:
            raise ValueError("Invalid space")
        start, end = timestamp(row["start_time"]), timestamp(row["end_time"])
        if end <= start or row["status"] not in {"confirmed", "cancelled", "completed"}:
            raise ValueError("Invalid booking")
        for name in ("actual_start", "actual_end"):
            if row.get(name) is not None:
                timestamp(row[name])
        rate, estimate = float(row["hourly_rate"]), float(row["estimated_total"])
        if not math.isfinite(rate) or not math.isfinite(estimate) or min(rate, estimate) < 0:
            raise ValueError("Invalid price")
        bookings.append({"id": row["id"], "parking_space_id": row["parking_space_id"],
            "label": by_id[row["parking_space_id"]]["label"], "start_time": row["start_time"],
            "end_time": row["end_time"], "actual_start": row.get("actual_start"),
            "actual_end": row.get("actual_end"), "status": row["status"],
            "hourly_rate": rate, "estimated_total": estimate})
    return {"spaces": spaces, "bookings": bookings, "schedule_date": data["schedule_date"],
            "server_time": data["server_time"], "timezone": data["timezone"], "source": "supabase"}


def create_booking_blueprint(client_factory, verify_user):
    pages = Blueprint("bookings", __name__)

    def authenticated_client():
        if not session.get("access_token") or not session.get("refresh_token"):
            raise AuthError("Sign in required", None)
        client = client_factory()
        verify_user(client)
        client.postgrest.auth(session["access_token"])
        return client

    def error_response(error):
        if isinstance(error, AuthError) and not isinstance(error, AuthRetryableError):
            session.clear()
            return jsonify(error="Please sign in again to manage your bookings.", code="auth_required"), 401
        if isinstance(error, APIError):
            if error.code == "PT400":
                return jsonify(error="Choose a start and end time between now and midnight today."), 400
            if error.code == "PT401":
                return jsonify(error="Please sign in again.", code="auth_required"), 401
            if error.code == "PT404":
                return jsonify(error="This booking or parking space is not available."), 404
            if error.code == "PT409":
                return jsonify(error="This space or booking is no longer available for that action. Refresh and check the time."), 409
            if error.code in {"PGRST202", "PGRST205", "42P01"}:
                return jsonify(error="Booking is not available yet. Please try again later."), 503
        return jsonify(error="We could not reach the parking service. Refresh to check the latest information."), 503

    def user_page(page):
        try:
            authenticated_client()
        except (AuthRetryableError, httpx.HTTPError, RuntimeError):
            return render_template("unavailable.html"), 503
        except AuthError:
            session.clear()
            flash("Sign in to manage your bookings.")
            return redirect(url_for("index"))
        space = request.args.get("space", "")
        selected_space = int(space) if space.isdigit() and len(space) < 11 else None
        return render_template("user_pages.html", user_page=page, selected_space=selected_space)

    @pages.get("/booking")
    def booking_page():
        return user_page("booking")

    @pages.get("/active-parking")
    def active_page():
        return user_page("active")

    @pages.get("/api/bookings")
    def overview():
        try:
            data = authenticated_client().rpc("smart_parking_user_schedule", {}).execute().data
            return jsonify(**booking_overview(data))
        except (AuthError, APIError, httpx.HTTPError, RuntimeError, ValueError, TypeError, KeyError) as error:
            return error_response(error)

    @pages.post("/api/bookings")
    def reserve():
        try:
            client = authenticated_client()
        except (AuthError, httpx.HTTPError, RuntimeError) as error:
            return error_response(error)
        try:
            space_id = int(request.form["space_id"])
            day = date.fromisoformat(request.form["date"]).isoformat()
            start, end = request.form["start_time"], request.form["end_time"]
            pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
            if not 1 <= space_id <= 2147483647 or not re.fullmatch(pattern, start):
                raise ValueError("Invalid space or time")
            if not (end == "24:00" or re.fullmatch(pattern, end)) or end <= start:
                raise ValueError("Invalid interval")
        except (KeyError, ValueError, TypeError):
            return jsonify(error="Choose a parking space and a valid time interval."), 400
        try:
            result = client.rpc("smart_parking_user_action", {
                "p_action": "reserve", "p_space_id": space_id,
                "p_day": day, "p_start": start, "p_end": end,
            }).execute().data
            identifier = str(UUID(result["booking_id"]))
            return jsonify(ok=True, booking_id=identifier), 201
        except (AuthError, APIError, httpx.HTTPError, RuntimeError, ValueError, TypeError, KeyError) as error:
            return error_response(error)

    @pages.post("/api/bookings/<uuid:booking_id>/<action>")
    def change_booking(booking_id, action):
        if action not in {"start", "finish", "cancel"}:
            return jsonify(error="Choose a valid parking action."), 400
        try:
            authenticated_client().rpc("smart_parking_user_action", {
                "p_action": action, "p_booking_id": str(booking_id),
            }).execute()
            return jsonify(ok=True)
        except (AuthError, APIError, httpx.HTTPError, RuntimeError) as error:
            return error_response(error)

    return pages
