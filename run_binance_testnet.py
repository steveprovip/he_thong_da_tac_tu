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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == "scripts" else Path(__file__).resolve().parent
for p in (ROOT / "core", ROOT / "live", ROOT / "simulation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import random
from real_alpha_engine import Candle, RealAlphaEngine
from consensus_engine import AgentRegistry, ConsensusEngine, Proposal, Vote, VoteStance, ConsensusDecision
from groktopus_swarm import heuristic_brain

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


def fetch_candles_and_depth(symbol: str = "BTCUSDT", limit: int = 50) -> tuple[list[Candle], list[tuple[float, float]], list[tuple[float, float]]]:
    """Fetch live 1-minute klines and order book depth from Binance Futures Testnet."""
    # 1. Klines
    req_k = urllib.request.Request(
        f"{BASE_URL}/fapi/v1/klines?symbol={symbol}&interval=1m&limit={limit}",
        headers={"User-Agent": "GrokTOPUS/1.0"},
    )
    with urllib.request.urlopen(req_k, timeout=5) as resp:
        raw_klines = json.loads(resp.read().decode())
    candles = [
        Candle(
            timestamp=float(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        for k in raw_klines
    ]

    # 2. Depth
    req_d = urllib.request.Request(
        f"{BASE_URL}/fapi/v1/depth?symbol={symbol}&limit=20",
        headers={"User-Agent": "GrokTOPUS/1.0"},
    )
    with urllib.request.urlopen(req_d, timeout=5) as resp:
        raw_depth = json.loads(resp.read().decode())
    bids = [(float(b[0]), float(b[1])) for b in raw_depth.get("bids", [])]
    asks = [(float(a[0]), float(a[1])) for a in raw_depth.get("asks", [])]
    return candles, bids, asks


def run_autonomous_swarm(
    symbol: str = "BTCUSDT",
    interval_s: int = 10,
    default_qty: float = 0.002,
    stop_loss_pct: float = 1.0,
    take_profit_pct: float = 1.2,
):
    """
    Autonomous trading loop:
    1. Ingests live klines & order book from Binance Testnet.
    2. Computes statistical alpha indicators via RealAlphaEngine (no oracle leakage).
    3. 6 Analyst Agents deliberate (TIDAL, NORO, LUMEN, ZEPHR, OKAPI, RUNE).
    4. ConsensusEngine tallies weighted votes (Threshold >= 76.8%, RUNE veto).
    5. If authorised, VESKA places entry order + on-exchange protective stop-loss.
    6. MARIN monitors position PnL, trailing stops, and takes profit.
    """
    registry = AgentRegistry()
    engine = ConsensusEngine(registry)
    rng = random.Random()
    cycle_count = 0
    total_executed = 0

    while True:
        cycle_count += 1
        os.system("cls" if os.name == "nt" else "clear")
        print("==============================================================================")
        print("    🐙 GROKTOPUS - BINANCE TESTNET 8-AGENT AUTONOMOUS SWARM")
        print("==============================================================================")

        try:
            acc = get_account_info()
            wallet_bal = acc["wallet_balance"]
            avail_bal = acc["available_balance"]
            unrealized_pnl = acc["unrealized_pnl"]
            positions = acc["positions"]
        except Exception as e:
            print(f"  ⚠️ Lỗi đọc tài khoản Binance Testnet: {e}")
            time.sleep(3)
            continue

        try:
            candles, bids, asks = fetch_candles_and_depth(symbol, limit=50)
            spot = candles[-1].close if candles else float(get_ticker(symbol)["price"])
            snap = RealAlphaEngine.evaluate_market(
                symbol=symbol,
                candles=candles,
                bids=bids,
                asks=asks,
                current_equity=wallet_bal,
                allocated_margin=0.0,
            )
        except Exception as e:
            print(f"  ⚠️ Lỗi lấy dữ liệu thị trường: {e}")
            time.sleep(3)
            continue

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"  • Thời gian: {now_str} | Chu kỳ: #{cycle_count} | Trạng thái: TREO MÁY ĐANG CHẠY 🟢")
        print(f"  • Sàn: Binance Futures Testnet | Ví: ${wallet_bal:,.2f} USDT | PnL mở: ${unrealized_pnl:+,.2f} USDT")
        print(f"  • {symbol}: ${spot:,.2f} | RSI(14): {snap.rsi_14:.1f} | OBI: {snap.orderbook_imbalance:+.2f} | ATR%: {snap.atr_volatility_pct:.3f}% | Phí 2 chiều: {snap.cost_bps:.1f} bps")
        print("-" * 78)

        # MARIN: Monitor existing position
        if positions:
            p = positions[0]
            amt = float(p["positionAmt"])
            entry_px = float(p["entryPrice"])
            pnl = float(p["unrealizedProfit"])
            pos_side = "LONG (BUY)" if amt > 0 else "SHORT (SELL)"
            notional = abs(amt) * entry_px
            roe_pct = (pnl / notional * 100.0) if notional > 0 else 0.0

            print(f"  📍 VỊ THẾ ĐANG MỞ: {p['symbol']} | {pos_side} | KL: {abs(amt)} BTC | Giá vào: ${entry_px:,.2f}")
            print(f"     Lãi/Lỗ hiện tại: ${pnl:+,.2f} USDT ({roe_pct:+,.2f}%)")
            print(f"  🛡️ MARIN: Đang giám sát vị thế & Lệnh Stop-Loss bảo vệ đang treo trên sàn.")

            # Check Take Profit
            if roe_pct >= take_profit_pct:
                print(f"\n  🎯 [MARIN CHỐT LỜI THÀNH CÔNG] Đạt mục tiêu +{roe_pct:.2f}% (>= {take_profit_pct}%)!")
                print(f"     Tiến hành đóng vị thế và hủy lệnh Stop-Loss trên sàn...")
                flatten_all()
            elif roe_pct <= -(stop_loss_pct * 1.5):
                print(f"\n  ⚠️ [MARIN CẮT LỖ KHẨN CẤP] Vị thế vi phạm ngưỡng an toàn ({roe_pct:.2f}%)! Đang đóng vị thế...")
                flatten_all()
            else:
                print(f"  👉 MARIN tiếp tục giữ vị thế chờ chạm Take-Profit (+{take_profit_pct}%) hoặc Stop-Loss (-{stop_loss_pct}%).")
        else:
            print("  📍 VỊ THẾ HIỆN TẠI: Chưa có vị thế mở (Flat). Hội đồng 6 AI đang rà quét tín hiệu...")
            
            # Formulate proposed stance based on trend/momentum
            proposed_side = VoteStance.BUY if snap.momentum_sigma > 0 else VoteStance.SELL
            notional_cents = int(default_qty * spot * 100)
            
            context = snap.to_swarm_context(side_stance=proposed_side.value)
            context["side"] = proposed_side
            context["qty"] = int(default_qty * 1000)
            context["price_cents"] = int(spot * 100)
            context["notional_cents"] = notional_cents
            context["equity_cents"] = int(wallet_bal * 100)
            context["mispricing_bps"] = snap.forecast_bps
            context["step"] = cycle_count

            proposal = Proposal(
                proposal_id=f"PROP_{int(time.time()*1000)}",
                symbol=symbol,
                side=proposed_side,
                qty=context["qty"],
                price_cents=context["price_cents"],
            )

            # Deliberation across 6 analysts
            print(f"\n  📋 HỘI ĐỒNG 6 AI BIỂU QUYẾT CHO ĐỀ XUẤT [{proposed_side.value} {symbol}]:")
            votes: list[Vote] = []
            for spec in registry.analysts():
                res = heuristic_brain(spec, "", context, rng)
                vote = Vote(
                    agent=spec.name,
                    stance=VoteStance(res["stance"]),
                    confidence=res["confidence"],
                    rationale=res["rationale"],
                    veto=res.get("veto", False),
                )
                votes.append(vote)
                veto_tag = " ⛔ [VETO!]" if vote.veto else ""
                print(f"    • {spec.name:<6s} ({spec.role_vi or spec.role:<18s}): {vote.stance.value:<4s} | Tin cậy: {vote.confidence*100:5.1f}% | {vote.rationale}{veto_tag}")

            # Consensus tally
            print("-" * 78)
            c_res = engine.evaluate(proposal, votes)
            if c_res.authorised:
                print(f"  🚀 ĐỒNG THUẬN: {c_res.score_pct:.1f}% >= 76.8%  ==>  [EXECUTE] ĐẠT CHUẨN SIÊU ĐA SỐ!")
                print(f"  🔑 ConsensusEngine đã cấp mã Token: {c_res.token.token[:20]}... (Chữ ký bảo mật đơn hiệu)")
                print(f"  ⚡ VESKA kích hoạt vào lệnh thật trên Binance Futures Testnet...")
                try:
                    place_test_order(symbol=symbol, side=proposed_side.value, qty=default_qty, stop_loss_pct=stop_loss_pct)
                    total_executed += 1
                except Exception as e:
                    print(f"  ❌ Lỗi khi bắn lệnh: {e}")
            elif c_res.decision == ConsensusDecision.HOLD:
                print(f"  ⏸️ ĐỒNG THUẬN: {c_res.score_pct:.1f}% / 76.8%  ==>  [HOLD] CHƯA ĐẠT NGƯỠNG SIÊU ĐA SỐ")
                print(f"     (Tín hiệu chưa đủ hội tụ cao, 6 AI thống nhất giữ ${wallet_bal:,.2f} USDT an toàn, đợi chu kỳ sau)")
            elif c_res.decision == ConsensusDecision.VETO:
                print(f"  🛡️ ĐỒNG THUẬN: [VETO BÁC BỎ] Người gác cổng {c_res.veto_source or 'RUNE'} phủ quyết: {c_res.reason}")

        print("-" * 78)
        print(f"  ⏳ Đang đếm ngược {interval_s}s sang chu kỳ #{cycle_count + 1}... (Nhấn Ctrl+C để dừng)")
        try:
            for s in range(interval_s, 0, -1):
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n")
            print("=" * 78)
            print("    🛑 ĐÃ TẠM DỪNG CHẾ ĐỘ TREO MÁY TỰ ĐỘNG")
            print("==============================================================================")
            print(f"  • Tổng chu kỳ đã chạy: {cycle_count}")
            print(f"  • Số lệnh AI đã khớp:  {total_executed}")
            print()
            print("  [1] Tiếp tục treo máy tự động")
            print("  [2] Đóng khẩn cấp mọi vị thế & Hủy lệnh chờ (Flatten All)")
            print("  [0] Quay lại menu chính")
            print("==============================================================================")
            try:
                sub_c = input("👉 Nhập lựa chọn của bạn (0-2): ").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if sub_c == "1":
                continue
            elif sub_c == "2":
                flatten_all()
                input("\nNhấn Enter để quay lại menu...")
                break
            else:
                break


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--auto", "-a"):
        run_autonomous_swarm()
        return

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
        print("  [5] 🤖 BẬT CHẾ ĐỘ TREO MÁY TỰ ĐỘNG (8 AI Swarm Autonomous Live Trading)")
        print("  [0] Thoát (Quay lại)")
        print("==============================================================================")
        try:
            c = input("👉 Nhập lựa chọn của bạn (0-5): ").strip()
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
        elif c == "5":
            run_autonomous_swarm()
        elif c == "0":
            break


if __name__ == "__main__":
    main()
