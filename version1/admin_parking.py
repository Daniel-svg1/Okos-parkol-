import hmac
import re
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from threading import Lock
from uuid import UUID

import httpx
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for
from postgrest.exceptions import APIError
from supabase import ClientOptions, create_client

from parking_data import normalize_spaces


admin = Blueprint("admin", __name__)
failed_attempts = defaultdict(deque)
attempt_lock = Lock()


def admin_client():
    settings = current_app.config
    if not all(settings.get(name) for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "PARKING_ADMIN_SECRET")):
        raise RuntimeError("Missing server configuration")
    return create_client(settings["SUPABASE_URL"], settings["SUPABASE_PUBLISHABLE_KEY"],
                         options=ClientOptions(auto_refresh_token=False, persist_session=False))


def admin_signed_in():
    expiry = session.get("admin_expires_at")
    return isinstance(expiry, (int, float)) and expiry > time.time()


def read_parking_map(client, admin_secret=None):
    name = "smart_parking_admin_map" if admin_secret else "smart_parking_map"
    params = {"p_server_key": admin_secret} if admin_secret else {}
    rows = client.rpc(name, params).execute().data
    return normalize_parking_map(rows)


def normalize_parking_map(rows):
    spaces = normalize_spaces(rows)
    reservations = {row["id"]: row for row in rows}
    for space in spaces:
        row = reservations[space["id"]]
        reserved = row.get("reserved", False)
        ends_at = row.get("reserved_until")
        if type(reserved) is not bool:
            raise ValueError("Invalid reservation state")
        if reserved:
            if not isinstance(ends_at, str):
                raise ValueError("Invalid reservation time")
            parsed = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Missing reservation timezone")
        space.update(reserved=reserved, reserved_until=ends_at if reserved else None)
    return spaces


def read_admin_schedule(client):
    data = client.rpc("smart_parking_admin_schedule", {
        "p_server_key": current_app.config["PARKING_ADMIN_SECRET"],
    }).execute().data
    spaces = normalize_parking_map(data["spaces"])
    date.fromisoformat(data["schedule_date"])
    datetime.fromisoformat(data["server_time"].replace("Z", "+00:00"))
    if data["timezone"] != "Europe/Bucharest" or not isinstance(data["reservations"], list):
        raise ValueError("Invalid reservation schedule")
    by_id = {space["id"]: space for space in spaces}
    for space in spaces:
        space["reservations"] = []
    for booking in data["reservations"]:
        UUID(booking["id"])
        start = datetime.fromisoformat(booking["start_time"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(booking["end_time"].replace("Z", "+00:00"))
        if not start.tzinfo or not end.tzinfo or end <= start:
            raise ValueError("Invalid reservation time")
        by_id[booking["parking_space_id"]]["reservations"].append({
            "id": booking["id"], "start_time": booking["start_time"], "end_time": booking["end_time"],
        })
    return {"spaces": spaces, "schedule_date": data["schedule_date"],
            "server_time": data["server_time"], "timezone": data["timezone"]}


def admin_error(error):
    if isinstance(error, APIError):
        if error.code == "PT400":
            return jsonify(error="Choose a start and end time between now and midnight today."), 400
        if error.code == "PT409":
            return jsonify(error="This space is occupied, the reservation overlaps another booking, or the booking has changed. Refresh and choose another time."), 409
        if error.code == "PT404":
            return jsonify(error="This parking space no longer exists."), 404
        if error.code in {"PGRST202", "PGRST205", "42P01", "42883", "PT403"}:
            return jsonify(error="The parking database is not ready yet.", code="setup_required"), 503
    if isinstance(error, RuntimeError):
        return jsonify(error="The Supabase admin connection is not configured yet.", code="setup_required"), 503
    return jsonify(error="The database could not be reached. Refresh to check the latest state.", code="unavailable"), 503


@admin.route("/admin", methods=["GET", "POST"])
def dashboard():
    if request.method == "POST":
        address = request.remote_addr or "local"
        now = time.monotonic()
        code = request.form.get("code", "")
        with attempt_lock:
            attempts = failed_attempts[address]
            while attempts and now - attempts[0] >= 300:
                attempts.popleft()
            if len(attempts) >= 5:
                return render_template("admin_login.html", error="Too many attempts. Try again in 5 minutes."), 429
            expected = current_app.config.get("ADMIN_CODE", "")
            if not expected or len(code) > 128 or not hmac.compare_digest(code.encode(), expected.encode()):
                attempts.append(now)
                return render_template("admin_login.html", error="Incorrect admin code."), 401
            attempts.clear()
        session["admin_expires_at"] = time.time() + 3600
        session.pop("csrf_token", None)
        return redirect(url_for("admin.dashboard"))
    if not admin_signed_in():
        session.pop("admin_expires_at", None)
        return render_template("admin_login.html")
    return render_template("inside.html", admin_mode=True)


@admin.post("/admin/logout")
def sign_out():
    session.pop("admin_expires_at", None)
    return redirect(url_for("admin.dashboard"))


@admin.get("/api/admin/parking-spaces")
def spaces_api():
    if not admin_signed_in():
        return jsonify(error="Enter your admin code to continue.", code="admin_required"), 401
    try:
        client = admin_client()
        data = read_admin_schedule(client)
        return jsonify(**data, source="supabase", fetched_at=datetime.now(timezone.utc).isoformat())
    except (APIError, httpx.HTTPError, RuntimeError, ValueError, TypeError, KeyError) as error:
        return admin_error(error)


@admin.post("/api/admin/parking-spaces/<int:space_id>")
def update_space(space_id):
    if not admin_signed_in():
        return jsonify(error="Enter your admin code to continue.", code="admin_required"), 401
    action = request.form.get("action", "")
    if action not in {"occupy", "free", "reserve", "move", "cancel"} or not 1 <= space_id <= 2147483647:
        return jsonify(error="Choose a valid parking action."), 400
    try:
        source_id = int(request.form["source_id"]) if action == "move" else None
        booking_id = str(UUID(request.form["booking_id"])) if action == "cancel" else None
        day = start = end = None
        if action == "reserve":
            day = date.fromisoformat(request.form["date"]).isoformat()
            start, end = request.form["start_time"], request.form["end_time"]
            clock_pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
            if not re.fullmatch(clock_pattern, start) or not (end == "24:00" or re.fullmatch(clock_pattern, end)):
                raise ValueError("Invalid time")
            if start >= end:
                raise ValueError("Invalid interval")
        if action == "move" and (source_id == space_id or not 1 <= source_id <= 2147483647):
            raise ValueError("Invalid action")
    except (ValueError, TypeError, KeyError):
        return jsonify(error="Choose a valid space, date and time interval."), 400
    try:
        admin_client().rpc("smart_parking_admin_action", {
            "p_action": action, "p_space_id": space_id,
            "p_source_id": source_id, "p_booking_id": booking_id,
            "p_day": day, "p_start": start, "p_end": end,
            "p_server_key": current_app.config["PARKING_ADMIN_SECRET"],
        }).execute()
        return jsonify(ok=True)
    except (APIError, httpx.HTTPError, RuntimeError) as error:
        return admin_error(error)
