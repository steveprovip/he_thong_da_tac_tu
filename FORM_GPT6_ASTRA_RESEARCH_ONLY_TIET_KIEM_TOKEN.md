# 🌌 FORM GPT-6 ASTRA MEDIUM × CODEX — CARRY RESEARCH PACK (v3, 10/09/2026)
### Target: `F:\he_thong_da_tac_tu_2` — nâng cấp FundingCarry theo chuẩn He-Manela-Ross (2024) + Gupta-Polson (2026)
### Vai trò: Codex CHỈ nghiên cứu + spec. Cline (tôi) chạy lệnh + code. Output <800 tokens.

> **Nguồn đã tra (10/09/2026) — nền tảng lý thuyết:**
> - **He, Manela, Ross, von Wachter (2024, arXiv:2212.06888v6)** — *Fundamentals of Perpetual Futures*: giá perp = PV benchmark flow chiết khấu theo funding rate; funding chọn cả benchmark lẫn discount; basis hội tụ về giá no-arbitrage (không phải spot); "implied arbitrage strategy yields high Sharpe ratios" — carry có cơ sở thật.
> - **Gupta & Polson (2026, arXiv:2609.05433)** — mở rộng Shiller (1993): price = expected spot tại event đầu của clock intensity=funding; **"stochastic volatility moves the basis only through the carry"** → rủi ro basis từ volatility premium, dùng cho sizing theo vol regime.
> - **Lalor & Swishchuk (2026, arXiv:2409.12721v3)** — fill probability + adverse fill làm thay đổi đáng kể performance; cẩn trọng backtest không queue/latency.
> - **Jebali (2026, arXiv:2609.07989)** — order flow có regimes → gate theo regime có cơ sở.
> - **Naviglio & Lillo (2026, arXiv:2609.06085)** — DL thường không hơn parametric → cẩn thận ML funding-predict.
> - **Binance mechanics (verify /fapi/v1/premiumIndex, 900 perp, 10/09)**: rate dương → short nhận; interval 8h mặc định (1 số symbol 4h/1h); cap ±0.3%/±2% tùy symbol; rate đổi mỗi interval.
> - **Số thật 10/09** (`phan_tich/soi_funding.py`): VTHO -162.5, IOST -74.8, NEWT -64.2, CXMT -46.1, LA -38.6 bps/8h → net sau phí 6 bps: +156/+68/+58/+40/+32 bps/8h. Biên gấp 25-160 lần chi phí.

---
## 📋 CÁCH DÙNG 2 PHÚT
1. VS Code → Open Folder `F:\he_thong_da_tac_tu_2` → Codex model **GPT-6 Astra Medium**.
2. Copy khối ```text ở PHẦN 2 → paste → Enter. Chỉ hỏi thêm khi tôi bảo (mỗi Q 1 dòng kèm Q0). Cấm nó tự chạy lệnh.

---
## PHẦN 2: KHỐI COPY-PASTE SANG CODEX

```text
Role: Quant researcher. RESEARCH ONLY, NO CODE, NO EDIT, NO CMDS. <800 tokens, VN, table only, no basics.
Ctx: GROKTOPUS futures-only Binance USD-M. Scalping 5phut KILLED (102 trips, net -6.72 USDT, edge -12.6bps KTC[-15.5,-9.7]). Chuyen sang FUNDING-CARRY theo He-Manela-Ross 2024 + Gupta-Polson 2026.
Real 10/09 (soi_funding.py, 900 perp, fee MEASURED 6bps=2in+4out): VTHO -162.5bps/8h (net +156.5, ~17100%/yr), IOST -74.8, NEWT -64.2, CXMT -46.1, LA -38.6, URNM +34.2, TEAM +30.3. Tuc la short coin funding am that manh, hoac long coin funding duong.
Code co san: core/funding_carry.py (FundingCarry+CarryConfig: fee 6.0bps, min_rate 5bps/8h, max_spread 15, stop 2xATR, horizon toi print+120s buffer, single-leg funded directional KHONG PHAI hedge that), runtime.maybe_carry READ-ONLY, phan_tich/carry_lab.py (forecast/settle/UNMATCHED), phan_tich/soi_funding.py, 272 tests pass. Gateway: 1 active/symbol, GTX post-only entry, STOP_MARKET closePosition, PositionSizer bootstrap 0.25 kelly, DrawdownBrake.
Binance mechanics: rate doi moi interval (8h mac dinh, 1 so symbol 4h/1h); cap ±0.3%/2% tuy symbol; rate duong=short nhan; ky ke tiep khong bao gom rate hien tai.
Task: spec nang cap FundingCarry thanh carry-harvester don gian, AN TOAN, duoc phep testnet trong phien nay. Tra bang | KL | So | File:Func | Pseudo 2d | Test | Kill |:
R1: Top-N coin theo |rate| NHUNG loc them: volume>=X, spread<=Y, OI>=Z (chong coin ma cua), max 3 pos dong thoi, notional <=1% equity/coin. N va X/Y/Z nghia la bao nhieu hop ly?
R2: Timing entry: vao truoc print bao lau? Neu rate flip giua luc vao va print thi sao? Co nen auto-exit khi rate dao dau?
R3: Risk squeeze: funding am cuc dai (VTHO -162) nghia la short rat dong, gia co the squeeze. Dung stop 2xATR duoc khong, hay can co che khac (max loss/pos, kill-switch theo ngay)?
R4: Exit: giu qua print roi thoat gap, hay giu nhieu ky neu rate van cao? Chi phi nhap lai 6bps moi ky thi net con bao nhieu khi chi giu 1 ky vs 3 ky?
R5: Con duong testnet: testnet co funding that khong, hay chi mainnet co? Neu testnet khong co, thu nghiem shadow voi gia mainnet thi do duoc gi va khong do duoc gi?
Kill-criteria tong: 5 phien lien tiep net_H thu duoc < 50% forecast => dung, bao cao. Cam: ha ZEPHR, ha 76.8, mo hedge gia, vuot 1% equity/coin, goi single-leg la "delta-neutral".
```
---
## PHẦN 3: CÂU HỎI THEO SAU (mỗi Q 1 chat mới, paste kèm Q0)

```text
R6: Envelope sizing theo Gupta-Polson (2026): "stochastic volatility moves the basis only through the carry" — dung y nay de scale notional theo vol regime cu the nao? Cho cong thuc.
R7: Funding 1h/4h symbol: uu tien interval ngan vi lai quay nhanh, hay interval dai vi it phi re-entry? Tinh diem hoa.
R8: Chong coin ma cua: nen lay data nao tu /fapi/v1/openInterest + /futures/data (taker buy/sell) de chan pump-dump? Spec 2 dong.
```
> Quy trình: paste Q0+R1 → bảng → về Cline tôi code → hỏi R2... Chỉ 1 Q/lần.

