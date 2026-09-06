---
name: binance-futures-algo-guard
description: Binance USD-M Futures API integration, HMAC-SHA256 signature protocol, Post-Only GTX maker routing, bracket order management (exchange-side STOP_MARKET / TAKE_PROFIT_MARKET), rate limit budget (1200 weight/min), and WebSocket user stream monitoring.
---

# Binance USD-M Futures Algorithmic Execution Guard

## 1. Network & API Specifications
- Testnet Endpoint: https://testnet.binancefuture.com
- Live Endpoint: https://fapi.binance.com
- Request Signing: Query string signed with HMAC-SHA256 using API Secret.
- Timestamp synchronization: Include millisecond timestamp within 1000ms drift window (recvWindow=5000).

## 2. Order Types & Execution Rules

### Post-Only (GTX) Entry
- Use type: 'LIMIT', timeInForce: 'GTX' (Good-Till-Crossing).
- Error Code -5022: 'Post Only order would immediately match and take.'
  * Action: Do not abort. Refresh book top, re-quote at new best bid/ask.

### On-Exchange Protective Stop Loss
Always place exchange-side stop immediately following entry fill:
- Use type: 'STOP_MARKET', stopPrice, closePosition: 'true'.
- Benefits: Guarantees position liquidation even if bot process, local server, or internet connection crashes.

## 3. Position Sizing & Precision Constraints
- Fetch /fapi/v1/exchangeInfo once at startup.
- Cache tickSize (price precision) and stepSize (quantity precision).
- Quantize all quantities: qty = floor(raw_qty / step_size) * step_size.
- Never send floating point values with excessive precision (causes error -1111 or -4003).

## 4. Rate Limit Management
- Binance USD-M Futures budget: 1,200 weight per minute per IP.
- /fapi/v1/order has weight 1.
- /fapi/v1/depth (limit 5) has weight 2.
- Inspect x-mbx-used-weight-1m header in responses. If used weight > 800, throttle polling interval.
