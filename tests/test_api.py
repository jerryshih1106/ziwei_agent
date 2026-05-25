"""Integration tests for new API routes using FastAPI TestClient.

Skipped automatically when heavy dependencies (langchain, linebot) are missing.
Run in the project's virtual environment for the full suite.
"""
import os
import sys

import pytest
from dotenv import load_dotenv

load_dotenv()

# Skip entire module if main.py can't be imported (missing langchain etc.)
try:
    import importlib
    import chat_bot.auth.auth as _auth_check
    # Attempt a lightweight check that won't fail due to AI deps
    _auth_check.init_db  # noqa: B018
    _app_importable = True
except Exception:
    _app_importable = False

pytestmark = pytest.mark.skipif(
    not _app_importable,
    reason="App dependencies (langchain etc.) not available in this env",
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient with a fresh temp database."""
    try:
        import langchain_core  # noqa: F401
        import linebot  # noqa: F401
    except ImportError:
        pytest.skip("Full stack (langchain_core + linebot) not available in this env")

    tmp = tmp_path_factory.mktemp("db")
    os.environ["DATA_DIR"] = str(tmp)
    os.environ["ALLOW_REGISTER"] = "true"

    import importlib
    import chat_bot.auth.auth as auth_mod
    importlib.reload(auth_mod)
    auth_mod.init_db()

    from fastapi.testclient import TestClient
    import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app, raise_server_exceptions=False) as c:
        yield c


def _register_and_login(client, username="user1", password="pass123"):
    os.environ["ALLOW_REGISTER"] = "true"
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    if r.status_code == 409:
        r = client.post("/api/auth/login", json={"username": username, "password": password})
    data = r.json()
    return data.get("token"), data.get("session_id")


# ── Auth endpoints ─────────────────────────────────────────────

def test_register(client):
    r = client.post("/api/auth/register", json={"username": "newuser", "password": "pass123"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_register_duplicate(client):
    client.post("/api/auth/register", json={"username": "dupuser", "password": "pass123"})
    r = client.post("/api/auth/register", json={"username": "dupuser", "password": "pass123"})
    assert r.status_code == 409


def test_login_success(client):
    client.post("/api/auth/register", json={"username": "loginuser", "password": "mypass"})
    r = client.post("/api/auth/login", json={"username": "loginuser", "password": "mypass"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={"username": "wpuser", "password": "correct"})
    r = client.post("/api/auth/login", json={"username": "wpuser", "password": "wrong"})
    assert r.status_code == 401


def test_me_authenticated(client):
    token, _ = _register_and_login(client, "meuser", "pass123")
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "meuser"


def test_me_unauthenticated(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


# ── Plan endpoint ──────────────────────────────────────────────

def test_get_plan_unauthenticated(client):
    r = client.get("/api/auth/plan")
    assert r.status_code == 401


def test_get_plan_default_free(client):
    token, _ = _register_and_login(client, "planuser", "pass123")
    r = client.get("/api/auth/plan", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["plan"] == "free"
    assert data["limit"] == 10


# ── Forgot / Reset password ────────────────────────────────────

def test_forgot_password_always_returns_ok(client):
    r = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_forgot_password_invalid_email(client):
    r = client.post("/api/auth/forgot-password", json={"email": "notanemail"})
    assert r.status_code == 422


def test_reset_password_flow(client):
    # Register user with email
    token, sid = _register_and_login(client, "resetuser", "oldpass")
    # Set email
    client.post(
        "/api/auth/update-email",
        json={"email": "reset@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Request reset (direct DB access for token)
    import chat_bot.auth.auth as auth_mod
    result = auth_mod.create_password_reset_token("reset@example.com")
    assert result is not None
    raw_token, username = result

    # Reset password via API
    r = client.post("/api/auth/reset-password", json={"token": raw_token, "new_password": "newpass123"})
    assert r.status_code == 200

    # Old password rejected
    r2 = client.post("/api/auth/login", json={"username": "resetuser", "password": "oldpass"})
    assert r2.status_code == 401

    # New password works
    r3 = client.post("/api/auth/login", json={"username": "resetuser", "password": "newpass123"})
    assert r3.status_code == 200


def test_reset_password_bad_token(client):
    r = client.post("/api/auth/reset-password", json={"token": "badtoken", "new_password": "newpass"})
    assert r.status_code == 400


def test_reset_password_too_short(client):
    r = client.post("/api/auth/reset-password", json={"token": "tok", "new_password": "123"})
    assert r.status_code == 400


# ── Update email / verify email ────────────────────────────────

def test_update_email_success(client):
    token, _ = _register_and_login(client, "emailuser", "pass123")
    r = client.post(
        "/api/auth/update-email",
        json={"email": "emailuser@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_update_email_unauthenticated(client):
    r = client.post("/api/auth/update-email", json={"email": "test@example.com"})
    assert r.status_code == 401


def test_verify_email_invalid_token(client):
    r = client.get("/api/auth/verify-email?token=badtoken")
    assert r.status_code == 400


def test_verify_email_valid_token(client):
    token, sid = _register_and_login(client, "verifyuser", "pass123")
    import chat_bot.auth.auth as auth_mod
    verify_tok = auth_mod.update_user_email(sid, "verify@example.com")
    r = client.get(f"/api/auth/verify-email?token={verify_tok}")
    assert r.status_code == 200


# ── Payment endpoints ──────────────────────────────────────────

def test_create_order_unauthenticated(client):
    r = client.post("/api/payment/create-order", json={"plan": "standard"})
    assert r.status_code == 401


def test_create_order_invalid_plan(client):
    token, _ = _register_and_login(client, "payuser", "pass123")
    r = client.post(
        "/api/payment/create-order",
        json={"plan": "enterprise"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_create_order_not_configured(client):
    """ECPay not configured in test env → 503."""
    token, _ = _register_and_login(client, "payuser2", "pass123")
    r = client.post(
        "/api/payment/create-order",
        json={"plan": "standard"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503


def test_payment_callback_bad_mac(client):
    r = client.post("/api/payment/callback", data={
        "MerchantTradeNo": "ORDER001",
        "RtnCode": "1",
        "TradeNo": "TRADE001",
        "CheckMacValue": "BADMAC",
    })
    assert r.status_code == 400


def test_payment_return_success(client):
    r = client.get("/api/payment/return?RtnCode=1&MerchantTradeNo=ORDER001")
    assert r.status_code == 200
    assert "付款成功" in r.text


def test_payment_return_fail(client):
    r = client.get("/api/payment/return?RtnCode=0")
    assert r.status_code == 200
    assert "付款未完成" in r.text


# ── Reset password page ────────────────────────────────────────

def test_reset_password_page(client):
    r = client.get("/reset-password?token=sometoken")
    assert r.status_code == 200
    assert "重置密碼" in r.text


# ── Change password ────────────────────────────────────────────

def test_change_password_flow(client):
    token, sid = _register_and_login(client, "chpwuser", "oldpass")
    r = client.post(
        "/api/auth/change-password",
        json={"old_password": "oldpass", "new_password": "newpass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


def test_change_password_wrong_old(client):
    token, _ = _register_and_login(client, "chpwuser2", "oldpass")
    r = client.post(
        "/api/auth/change-password",
        json={"old_password": "wrong", "new_password": "newpass"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
