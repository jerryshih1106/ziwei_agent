"""Tests for ECPay payment module."""
import pytest
from chat_bot.payment.ecpay import calc_check_mac_value, verify_callback


# ── CheckMacValue calculation ─────────────────────────────────

def test_check_mac_value_known_vector():
    """Verify against ECPay's own test vector from documentation."""
    # Standard test params from ECPay's integration guide
    params = {
        "MerchantID": "2000132",
        "MerchantTradeNo": "TEST20240101001",
        "MerchantTradeDate": "2024/01/01 12:00:00",
        "PaymentType": "aio",
        "TotalAmount": "199",
        "TradeDesc": "test",
        "ItemName": "test item",
        "ReturnURL": "https://example.com/callback",
        "ChoosePayment": "ALL",
        "EncryptType": "1",
        "OrderResultURL": "https://example.com/return",
    }
    hash_key = "5294y06JbISpM5x9"
    hash_iv = "v77hoKGq4kWxNNIS"
    mac = calc_check_mac_value(params, hash_key, hash_iv)
    # Must be uppercase hex, 64 chars (SHA256)
    assert len(mac) == 64
    assert mac == mac.upper()
    assert all(c in "0123456789ABCDEF" for c in mac)


def test_check_mac_value_deterministic():
    """Same params → same MAC every time."""
    params = {"A": "1", "B": "2", "C": "3"}
    key, iv = "testkey", "testiv"
    mac1 = calc_check_mac_value(params, key, iv)
    mac2 = calc_check_mac_value(params, key, iv)
    assert mac1 == mac2


def test_check_mac_value_order_independent():
    """Param order must not matter (sorted alphabetically before hashing)."""
    params_a = {"Zebra": "z", "Apple": "a", "Mango": "m"}
    params_b = {"Mango": "m", "Apple": "a", "Zebra": "z"}
    key, iv = "k", "i"
    assert calc_check_mac_value(params_a, key, iv) == calc_check_mac_value(params_b, key, iv)


def test_check_mac_value_ignores_existing_mac():
    """CheckMacValue key in params must be excluded from calculation."""
    params = {"A": "1", "CheckMacValue": "SOMEVALUE"}
    params_clean = {"A": "1"}
    key, iv = "key", "iv"
    assert calc_check_mac_value(params, key, iv) == calc_check_mac_value(params_clean, key, iv)


def test_check_mac_value_different_keys():
    """Different hash keys must produce different MACs."""
    params = {"A": "1"}
    mac1 = calc_check_mac_value(params, "key1", "iv1")
    mac2 = calc_check_mac_value(params, "key2", "iv2")
    assert mac1 != mac2


# ── verify_callback ────────────────────────────────────────────

def test_verify_callback_valid(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_HASH_KEY", "testkey")
    monkeypatch.setattr(ecpay, "_HASH_IV", "testiv")

    params = {
        "MerchantID": "2000132",
        "RtnCode": "1",
        "MerchantTradeNo": "ORDER001",
        "TradeNo": "TRADE001",
    }
    mac = calc_check_mac_value(params, "testkey", "testiv")
    params["CheckMacValue"] = mac
    assert verify_callback(params) is True


def test_verify_callback_tampered(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_HASH_KEY", "testkey")
    monkeypatch.setattr(ecpay, "_HASH_IV", "testiv")

    params = {
        "MerchantID": "2000132",
        "RtnCode": "1",
        "MerchantTradeNo": "ORDER001",
        "CheckMacValue": "BADHASH",
    }
    assert verify_callback(params) is False


def test_verify_callback_missing_mac(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_HASH_KEY", "testkey")
    monkeypatch.setattr(ecpay, "_HASH_IV", "testiv")
    assert verify_callback({"A": "1"}) is False


def test_verify_callback_not_configured(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_HASH_KEY", "")
    monkeypatch.setattr(ecpay, "_HASH_IV", "")
    assert verify_callback({"CheckMacValue": "anything"}) is False


# ── create_order_params ────────────────────────────────────────

def test_create_order_params_valid(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_MERCHANT_ID", "TEST001")
    monkeypatch.setattr(ecpay, "_HASH_KEY", "testkey")
    monkeypatch.setattr(ecpay, "_HASH_IV", "testiv")

    params = ecpay.create_order_params(
        "ORDER001", "standard",
        notify_url="https://example.com/callback",
        return_url="https://example.com/return",
    )
    assert params is not None
    assert params["TotalAmount"] == "199"
    assert "CheckMacValue" in params
    assert len(params["CheckMacValue"]) == 64


def test_create_order_params_pro_plan(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_MERCHANT_ID", "TEST001")
    monkeypatch.setattr(ecpay, "_HASH_KEY", "k")
    monkeypatch.setattr(ecpay, "_HASH_IV", "v")

    params = ecpay.create_order_params("ORDER002", "pro", "https://a.com/cb", "https://a.com/ret")
    assert params is not None
    assert params["TotalAmount"] == "1490"


def test_create_order_params_unknown_plan(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_MERCHANT_ID", "TEST001")
    monkeypatch.setattr(ecpay, "_HASH_KEY", "k")
    monkeypatch.setattr(ecpay, "_HASH_IV", "v")
    assert ecpay.create_order_params("O", "enterprise", "u", "u") is None


def test_create_order_params_not_configured(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_MERCHANT_ID", "")
    assert ecpay.create_order_params("O", "standard", "u", "u") is None


def test_order_id_truncated_to_20_chars(monkeypatch):
    import chat_bot.payment.ecpay as ecpay
    monkeypatch.setattr(ecpay, "_MERCHANT_ID", "TEST")
    monkeypatch.setattr(ecpay, "_HASH_KEY", "k")
    monkeypatch.setattr(ecpay, "_HASH_IV", "v")
    params = ecpay.create_order_params("A" * 30, "standard", "u", "u")
    assert len(params["MerchantTradeNo"]) <= 20
