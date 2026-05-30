"""ECPay AIO (All-In-One) payment integration for Taiwan market."""
import hashlib
import os
import time
import urllib.parse
from typing import Any
import urllib.request

_MERCHANT_ID = os.environ.get("ECPAY_MERCHANT_ID", "")
_HASH_KEY = os.environ.get("ECPAY_HASH_KEY", "")
_HASH_IV = os.environ.get("ECPAY_HASH_IV", "")
_IS_STAGE = os.environ.get("ECPAY_STAGE", "true").lower() != "false"

CHECKOUT_URL = (
    "https://payment-stage.ecpay.com.tw/Checkout/AioCheckout"
    if _IS_STAGE
    else "https://payment.ecpay.com.tw/Checkout/AioCheckout"
)

PLAN_ITEMS: dict[str, dict] = {
    "standard": {"name": "紫微AI命理 標準月費方案", "amount": 199},
    "pro": {"name": "紫微AI命理 專業年費方案", "amount": 1490},
}


def calc_check_mac_value(params: dict[str, Any], hash_key: str, hash_iv: str) -> str:
    """Compute ECPay CheckMacValue per official spec."""
    filtered = {k: v for k, v in params.items() if k != "CheckMacValue"}
    # Sort case-insensitively by key
    sorted_pairs = sorted(filtered.items(), key=lambda x: x[0].lower())
    joined = "&".join(f"{k}={v}" for k, v in sorted_pairs)
    raw = f"HashKey={hash_key}&{joined}&HashIV={hash_iv}"
    # URL-encode then lowercase (matches .NET HttpUtility.UrlEncode behaviour)
    encoded = urllib.parse.quote_plus(raw).lower()
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()


def create_order_params(
    order_id: str,
    plan: str,
    notify_url: str,
    return_url: str,
) -> "dict | None":
    """Build form params for ECPay AIO checkout. Returns None if plan unknown."""
    if plan not in PLAN_ITEMS:
        return None
    if not (_MERCHANT_ID and _HASH_KEY and _HASH_IV):
        return None
    item = PLAN_ITEMS[plan]
    trade_date = time.strftime("%Y/%m/%d %H:%M:%S")
    params: dict[str, Any] = {
        "MerchantID": _MERCHANT_ID,
        "MerchantTradeNo": order_id[:20],
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(item["amount"]),
        "TradeDesc": "紫微AI命理訂閱方案",
        "ItemName": item["name"],
        "ReturnURL": notify_url,
        "OrderResultURL": return_url,
        "ChoosePayment": "ALL",
        "EncryptType": "1",
    }
    params["CheckMacValue"] = calc_check_mac_value(params, _HASH_KEY, _HASH_IV)
    return params


def verify_callback(form_data: dict[str, str]) -> bool:
    """Verify CheckMacValue from ECPay callback POST."""
    if not (_HASH_KEY and _HASH_IV):
        return False
    received = form_data.get("CheckMacValue", "")
    expected = calc_check_mac_value(form_data, _HASH_KEY, _HASH_IV)
    return received.upper() == expected.upper()


def is_configured() -> bool:
    return bool(_MERCHANT_ID and _HASH_KEY and _HASH_IV)


_QUERY_URL = (
    "https://payment-stage.ecpay.com.tw/Cashier/QueryTradeInfo/V5"
    if _IS_STAGE
    else "https://payment.ecpay.com.tw/Cashier/QueryTradeInfo/V5"
)


def query_trade_status(order_id: str) -> "str | None":
    """Query ECPay for trade status of *order_id*.
    Returns '1' if paid, '0' or other string for other states, None on error.
    """
    if not is_configured():
        return None
    ts = str(int(time.time()))
    params: dict[str, Any] = {
        "MerchantID": _MERCHANT_ID,
        "MerchantTradeNo": order_id[:20],
        "TimeStamp": ts,
    }
    params["CheckMacValue"] = calc_check_mac_value(params, _HASH_KEY, _HASH_IV)
    body = urllib.parse.urlencode(params).encode("utf-8")
    try:
        req = urllib.request.Request(_QUERY_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        # Response format: "TradeStatus=1&MerchantTradeNo=...&..."
        pairs = dict(p.split("=", 1) for p in text.split("&") if "=" in p)
        return pairs.get("TradeStatus")
    except Exception:
        return None
