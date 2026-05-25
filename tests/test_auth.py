"""Tests for auth.py — user management, plan, password reset, rate limiting."""
import os
import sys
import tempfile
import time

import pytest

# Ensure dotenv is loaded before importing auth
from dotenv import load_dotenv
load_dotenv()

# Use a fresh in-memory/temp DB for each test
@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_users.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Force re-import with fresh DB path
    import importlib
    import chat_bot.auth.auth as auth_mod
    importlib.reload(auth_mod)
    auth_mod.init_db()
    yield auth_mod


def _make_user(auth, username="testuser", password="pass123", email=None):
    user = auth.create_user(username, password)
    assert user is not None
    if email:
        auth.update_user_email(user["session_id"], email)
    return user


# ── create_user / authenticate_user ───────────────────────────

def test_create_user_success(_temp_db):
    auth = _temp_db
    user = auth.create_user("alice", "secret123")
    assert user is not None
    assert user["username"] == "alice"
    assert len(user["session_id"]) > 10


def test_create_user_duplicate(_temp_db):
    auth = _temp_db
    auth.create_user("bob", "pass1")
    result = auth.create_user("bob", "pass2")
    assert result is None


def test_authenticate_success(_temp_db):
    auth = _temp_db
    auth.create_user("carol", "mypassword")
    user = auth.authenticate_user("carol", "mypassword")
    assert user is not None
    assert user["username"] == "carol"


def test_authenticate_wrong_password(_temp_db):
    auth = _temp_db
    auth.create_user("dave", "correct")
    assert auth.authenticate_user("dave", "wrong") is None


def test_authenticate_unknown_user(_temp_db):
    auth = _temp_db
    assert auth.authenticate_user("nobody", "pass") is None


# ── JWT token ─────────────────────────────────────────────────

def test_create_and_verify_token(_temp_db):
    auth = _temp_db
    user = auth.create_user("eve", "pass123")
    token = auth.create_token(user["username"], user["session_id"])
    payload = auth.verify_token(token)
    assert payload is not None
    assert payload["username"] == "eve"
    assert payload["session_id"] == user["session_id"]


def test_verify_invalid_token(_temp_db):
    auth = _temp_db
    assert auth.verify_token("invalid.token.here") is None


# ── Password reset ─────────────────────────────────────────────

def test_password_reset_flow(_temp_db):
    auth = _temp_db
    user = _make_user(auth, email="test@example.com")
    result = auth.create_password_reset_token("test@example.com")
    assert result is not None
    raw_token, username = result
    assert username == "testuser"
    assert len(raw_token) > 20

    # Verify and consume
    ok = auth.verify_and_consume_reset_token(raw_token, "newpassword123")
    assert ok is True

    # Old password no longer works
    assert auth.authenticate_user("testuser", "pass123") is None
    # New password works
    assert auth.authenticate_user("testuser", "newpassword123") is not None


def test_password_reset_wrong_email(_temp_db):
    auth = _temp_db
    _make_user(auth)
    result = auth.create_password_reset_token("nobody@example.com")
    assert result is None


def test_password_reset_invalid_token(_temp_db):
    auth = _temp_db
    _make_user(auth, email="t@example.com")
    ok = auth.verify_and_consume_reset_token("invalidtoken123", "newpass")
    assert ok is False


def test_password_reset_token_not_reusable(_temp_db):
    auth = _temp_db
    _make_user(auth, email="t2@example.com")
    result = auth.create_password_reset_token("t2@example.com")
    raw_token, _ = result
    # First use succeeds
    assert auth.verify_and_consume_reset_token(raw_token, "newpass1") is True
    # Second use fails (token consumed)
    assert auth.verify_and_consume_reset_token(raw_token, "newpass2") is False


# ── Email verification ─────────────────────────────────────────

def test_email_update_and_verify(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    token = auth.update_user_email(user["session_id"], "verify@example.com")
    assert token is not None
    plan = auth.get_user_plan(user["session_id"])
    assert plan["email"] == "verify@example.com"
    assert plan["email_verified"] is False

    ok = auth.verify_email_token(token)
    assert ok is True
    plan = auth.get_user_plan(user["session_id"])
    assert plan["email_verified"] is True


def test_email_verify_invalid_token(_temp_db):
    auth = _temp_db
    assert auth.verify_email_token("badtoken") is False


# ── Plan management ────────────────────────────────────────────

def test_default_plan_is_free(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    plan = auth.get_user_plan(user["session_id"])
    assert plan["plan"] == "free"
    assert plan["limit"] == 10


def test_activate_standard_plan(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    ok = auth.activate_plan(user["session_id"], "standard", 30)
    assert ok is True
    plan = auth.get_user_plan(user["session_id"])
    assert plan["plan"] == "standard"
    assert plan["limit"] == 200


def test_activate_pro_plan(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    ok = auth.activate_plan(user["session_id"], "pro", 365)
    assert ok is True
    plan = auth.get_user_plan(user["session_id"])
    assert plan["plan"] == "pro"
    assert plan["limit"] == 9999


# ── Rate limiting ──────────────────────────────────────────────

def test_rate_limit_free_plan(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    sid = user["session_id"]

    # Free plan: 10 calls allowed
    for i in range(10):
        allowed, remaining = auth.check_and_increment_api_calls(sid)
        assert allowed is True, f"Call {i+1} should be allowed"
        assert remaining == 9 - i

    # 11th call denied
    allowed, remaining = auth.check_and_increment_api_calls(sid)
    assert allowed is False
    assert remaining == 0


def test_rate_limit_paid_plan_not_blocked(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    sid = user["session_id"]
    auth.activate_plan(sid, "standard", 30)

    # Standard plan: 200 calls — spot check first 12
    for i in range(12):
        allowed, _ = auth.check_and_increment_api_calls(sid)
        assert allowed is True


def test_rate_limit_unknown_session(_temp_db):
    auth = _temp_db
    # Unknown session_id → allow through
    allowed, remaining = auth.check_and_increment_api_calls("unknown-session-xyz")
    assert allowed is True


def test_rate_limit_resets_next_day(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    sid = user["session_id"]

    # Exhaust today's limit
    for _ in range(10):
        auth.check_and_increment_api_calls(sid)

    # Manually set reset date to yesterday
    import sqlite3
    with auth.get_db() as conn:
        conn.execute(
            "UPDATE users SET api_calls_reset_at=? WHERE session_id=?",
            ("2000-01-01 00:00:00", sid),
        )
        conn.commit()

    # Should be allowed again
    allowed, remaining = auth.check_and_increment_api_calls(sid)
    assert allowed is True
    assert remaining == 9


# ── Payment orders ─────────────────────────────────────────────

def test_create_and_complete_order(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    sid = user["session_id"]

    ok = auth.create_payment_order("ORDER001", sid, "standard", 199)
    assert ok is True

    result = auth.complete_payment_order("ORDER001", "TRADE001")
    assert result is not None
    assert result["plan"] == "standard"
    assert result["session_id"] == sid


def test_complete_order_not_found(_temp_db):
    auth = _temp_db
    result = auth.complete_payment_order("NONEXISTENT", "T001")
    assert result is None


def test_complete_order_not_reusable(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    auth.create_payment_order("ORDER002", user["session_id"], "pro", 1490)
    auth.complete_payment_order("ORDER002", "TRADE002")
    # Second completion returns None (already paid)
    result = auth.complete_payment_order("ORDER002", "TRADE002B")
    assert result is None


def test_get_user_by_email(_temp_db):
    auth = _temp_db
    user = _make_user(auth, email="find@example.com")
    found = auth.get_user_by_email("find@example.com")
    assert found is not None
    assert found["session_id"] == user["session_id"]


def test_get_user_by_email_not_found(_temp_db):
    auth = _temp_db
    assert auth.get_user_by_email("nobody@example.com") is None


def test_get_user_by_session(_temp_db):
    auth = _temp_db
    user = _make_user(auth)
    found = auth.get_user_by_session(user["session_id"])
    assert found is not None
    assert found["username"] == "testuser"
