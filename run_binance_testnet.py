#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_binance_testnet.py - Interactive Binance Futures Testnet Manager for GrokTOPUS.
Allows the operator to check balances, place protected test orders, and manage positions
directly on Binance Futures Testnet using the credentials in .env.local.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == "scripts" else Path(__file__).resolve().parent
for p in (ROOT / "core", ROOT / "live", ROOT / "simulation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

BASE_URL = "https://testnet.binancefuture.com"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for f in (ROOT / ".env.local", ROOT / ".env"):
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k not in env:
                        env[k] = v
                        os.environ[k] = v
    return env


ENV = load_env()
API_KEY = ENV.get("BINANCE_TESTNET_API_KEY", "")
SECRET_KEY = ENV.get("BINANCE_TESTNET_SECRET_KEY", "")


def get_server_time() -> int:
    req = urllib.request.Request(f"{BASE_URL}/fapi/v1/time", headers={"User-Agent": "GrokTOPUS/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())["serverTime"]


def signed_request(method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not API_KEY or not SECRET_KEY:
        raise ValueError("Chưa tìm thấy BINANCE_TESTNET_API_KEY hoặc SECRET_KEY trong .env.local")
    params = params or {}
    stime = get_server_time()
    params["timestamp"] = stime
    params["recvWindow"] = 10000
    qs = urllib.parse.urlencode([(k, str(v)) for k, v in sorted(params.items())])
    sig = hmac.new(SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
    full_url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    
    headers = {"X-MBX-APIKEY": API_KEY, "User-Agent": "GrokTOPUS/1.0"}
    req = urllib.request.Request(full_url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Lỗi Binance API ({exc.code}): {err_msg}") from exc


def get_account_info():
    data = signed_request("GET", "/fapi/v2/account")
    usdt = next((a for a in data.get("assets", []) if a.get("asset") == "USDT"), {})
    positions = [
        p for p in data.get("positions", [])
        if abs(float(p.get("positionAmt", "0"))) > 0
    ]
    return {
        "wallet_balance": float(usdt.get("walletBalance", 0.0)),
        "available_balance": float(usdt.get("availableBalance", 0.0)),
        "unrealized_pnl": float(usdt.get("unrealizedProfit", 0.0)),
        "positions": positions,
    }


def get_ticker(symbol: str = "BTCUSDT"):
    req = urllib.request.Request(f"{BASE_URL}/fapi/v1/ticker/price?symbol={symbol}", headers={"User-Agent": "GrokTOPUS/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def place_test_order(symbol: str = "BTCUSDT", side: str = "BUY", qty: float = 0.002, stop_loss_pct: float = 1.0):
    print(f"\n  🚀 Đang gửi lệnh {side} {qty} {symbol} lên Binance Futures Testnet...")
    cid = f"GROK_TEST_{int(time.time()*1000)}"
    
    # 1. Entry Market Order
    entry_resp = signed_request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": str(qty),
        "newClientOrderId": cid,
    })
    fill_price = float(entry_resp.get("avgPrice") or 0.0)
    if fill_price <= 0:
        fill_price = float(get_ticker(symbol)["price"])
    
    print(f"  ✅ Lệnh khớp thành công! Mã lệnh: {entry_resp.get('orderId')} | Giá khớp: ${fill_price:,.2f}")
    
    # 2. Place On-Exchange Protective Stop Loss
    opp_side = "SELL" if side == "BUY" else "BUY"
    if side == "BUY":
        stop_price = round(fill_price * (1.0 - stop_loss_pct / 100.0), 1)
    else:
        stop_price = round(fill_price * (1.0 + stop_loss_pct / 100.0), 1)
        
    stop_cid = f"STOP_{cid}"[:36]
    print(f"  🛡️ Đang đặt lệnh cắt lỗ trên sàn (Stop-Loss) tại giá: ${stop_price:,.2f} ({opp_side})...")
    stop_resp = signed_request("POST", "/fapi/v1/order", {
        "symbol": symbol,
        "side": opp_side,
        "type": "STOP_MARKET",
        "stopPrice": str(stop_price),
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "newClientOrderId": stop_cid,
    })
    print(f"  ✅ Đã đặt lệnh cắt lỗ thật trên sàn Binance Testnet! Mã Stop: {stop_resp.get('orderId')}")
    print(f"  👉 Bạn có thể mở web demo.binance.com sẽ thấy vị thế và lệnh Stop hiển thị trực tiếp!")
    return entry_resp, stop_resp


def flatten_all():
    acc = get_account_info()
    positions = acc["positions"]
    if not positions:
        print("  ℹ️ Hiện không có vị thế nào đang mở.")
        return
    print(f"  ⚡ Đang đóng khẩn cấp {len(positions)} vị thế...")
    for p in positions:
        sym = p["symbol"]
        amt = float(p["positionAmt"])
        side = "SELL" if amt > 0 else "BUY"
        qty = abs(amt)
        try:
            signed_request("POST", "/fapi/v1/order", {
                "symbol": sym,
                "side": side,
                "type": "MARKET",
                "quantity": str(qty),
                "reduceOnly": "true",
            })
            print(f"    - Đã đóng vị thế {sym} (khối lượng {qty})")
        except Exception as e:
            print(f"    - Lỗi khi đóng {sym}: {e}")
            
    # Cancel all open stop orders
    for p in positions:
        try:
            signed_request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": p["symbol"]})
            print(f"    - Đã hủy các lệnh chờ của {p['symbol']}")
        except Exception:
            pass
    print("  ✅ Đã đóng toàn bộ vị thế an toàn!")


def main():
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print("==============================================================================")
        print("    🐙 GROKTOPUS - BINANCE FUTURES TESTNET CONTROLLER")
        print("==============================================================================")
        try:
            acc = get_account_info()
            print(f"  • Máy chủ sàn: {BASE_URL} (Binance Demo/Testnet)")
            print(f"  • Số dư ví:      ${acc['wallet_balance']:,.2f} USDT")
            print(f"  • Số dư khả dụng: ${acc['available_balance']:,.2f} USDT")
            print(f"  • Lãi lỗ mở (PnL): ${acc['unrealized_pnl']:+,.2f} USDT")
            print()
            if acc["positions"]:
                print("  📍 VỊ THẾ ĐANG MỞ TRÊN BINANCE TESTNET:")
                for p in acc["positions"]:
                    side = "LONG (BUY)" if float(p['positionAmt']) > 0 else "SHORT (SELL)"
                    print(f"    - {p['symbol']} | {side} | KL: {abs(float(p['positionAmt']))} | Giá vào: ${float(p['entryPrice']):,.2f} | Lãi/Lỗ: ${float(p['unrealizedProfit']):+,.2f}")
            else:
                print("  📍 VỊ THẾ ĐANG MỞ: (Không có vị thế nào)")
        except Exception as e:
            print(f"  ⚠️ Lỗi kết nối tài khoản: {e}")
            
        print("-" * 78)
        print("  [1] Làm mới thông tin tài khoản (Refresh)")
        print("  [2] Bắn 1 lệnh thử nghiệm LONG 0.002 BTC (kèm Stop-Loss trên sàn)")
        print("  [3] Bắn 1 lệnh thử nghiệm SHORT 0.002 BTC (kèm Stop-Loss trên sàn)")
        print("  [4] Đóng toàn bộ vị thế & Hủy lệnh chờ ngay lập tức (Flatten All)")
        print("  [0] Thoát (Quay lại)")
        print("==============================================================================")
        try:
            c = input("👉 Nhập lựa chọn của bạn (0-4): ").strip()
        except (KeyboardInterrupt, EOFError):
            break
            
        if c == "1":
            continue
        elif c == "2":
            try:
                place_test_order(symbol="BTCUSDT", side="BUY", qty=0.002, stop_loss_pct=1.0)
            except Exception as e:
                print(f"  ❌ Lỗi: {e}")
            input("\nNhấn Enter để tiếp tục...")
        elif c == "3":
            try:
                place_test_order(symbol="BTCUSDT", side="SELL", qty=0.002, stop_loss_pct=1.0)
            except Exception as e:
                print(f"  ❌ Lỗi: {e}")
            input("\nNhấn Enter để tiếp tục...")
        elif c == "4":
            flatten_all()
            input("\nNhấn Enter để tiếp tục...")
        elif c == "0":
            break


if __name__ == "__main__":
    main()
