# Quyet dinh Carry Funding — DUNG NGOAI (10/09/2026)

## 1. Tom tat nghien cuu R1-R4 (Codex GPT-6 Astra Medium, da verify bang lenh that)

| Q | Ket luan Codex | Verify thuc te (toi chay) |
|---|---|---|
| R1 | Chuan loc: \|rate\|>=30bps, vol>=50M, spread<=3bps, tuoi>=30 ngay; khong noi nguong de cho coin lot | Chay soi_funding.py tren 900 perp: **chi 1 coin gan dat = VTHO**; VTHO fail OI 7M<20M va spread 4.89>3 |
| Chieu | Rate am -> LONG nhan funding; duong -> SHORT tra | Xac nhan: funding_carry.plan() da dung ('SELL' if rate>0 else 'BUY'); loi chi trong chu cua form cu |
| R2 | Gap mark/idx khong phai loi chac; PnL gia = P_exit/P_entry-1; r=(I1/I0)x(1+b1)/(1+b0)-1 | Do that: VTHO mark/idx -4.69%, ask/idx -4.61%, ATR_1m 1.79%, 200 nen 5m bien do 65% |
| R3 | Stop 2xATR dong bang tai entry; kill theo basis delta tu entry; size = min(1% equity, budget/stop) | Stop 2xATR_1m = 3.57%; size thuc te 0.56% equity; 1 stop = mat ~1.87 ky funding (~15h), khong phai 2 ngay |
| R4 | Quyet dinh B: DUNG NGOAI. Khong noi OI/spread chi de VTHO lot. Testnet rate khac 0 chua chung settlement | **Toi verify them: testnet CO settlement that** — /fapi/v1/income tra FUNDING_FEE BTCUSDT -0.0523 USDT (time 1788566400). Co che co that, nhung khong thay doi quyet dinh vi chi 1/900 coin pass |

## 2. Bang chung chot

- Universe 900 perp, loc lien tiep: rate>=30 -> 3 coin, +vol>=50M -> 1, +spread<=3 -> 1, +tuoi>=30d -> **1 (VTHO)**.
- VTHO: OI 7.07M USDT (OI/CMC ~11%), spread 4.89bps, gap mark/idx **-4.69%** (perp rat thap hon index), ATR_1m 1.79%, bien do 200 nen 5m = 65%. Day la trang thai squeeze nguoc — long thu 197bps/8h nhung rui ro gia lam mat thu hoan toan.
- Testnet: CO settlement FUNDING_FEE that (bang chung income log). Nhung co che co that khong dua thanh edge kinh te.

## 3. Quyet dinh

1. **Dung ngoai** — khong probe VTHO, khong noi chuan, khong sua code carry ngay.
2. **Dieu kien xem lai** (tat ca phai cung thoa):
   - Co it nhat 2-3 coin dat chuan cross (khong phai 1).
   - Gap mark/idx cua ung vien nam trong |basis|<2%.
   - ATR_1m cua ung vien <= 1.0%.
   - (Neu da qua) testnet probe 1-2 phien nhan FUNDING_FEE settlement that va net_H khop forecast.
3. **Cong cu giu lai**: `phan_tich/soi_funding.py` (da nang cap cross-filter), `phan_tich/soi_liquidity.py` (chi tiet OI/spread/gap), `tools/check_testnet_funding_settlement.py` (verify settlement).
4. **Khong xoa khong refactor** code carry hien tai — giu nhu research-only, chua du dieu kien bat.

## 4. Trang thai he thong 10/09

- 272 tests pass. Scalping KILL (net -6.72 / edge -12.6bps). Carry: dung ngoai. He chay o che do do luong (paper + L2 + soi funding), khong co vi the that nao dang mo.
- Neu muon chay lai soi funding: `python phan_tich/soi_funding.py` (khong key, khong lenh, chi doc).

## 5. T5 — Kiem dinh L2 microprice/OFI (10/09, 1-3h nhu ke hoach)

Chay `phan_tich/study_l2_signal.py` tren 2.2GB L2 that da ghi 08/09 (BTC 1.6GB, SOL 364MB, BNB 313MB — 3.94h lien tung moi coin, snapshot 1s).

| Coin | Signal tot nhat (holdout) | Gap | Bar 6bps |
|---|---|---|---|
| BTCUSDT | MICROGAP h=60s | **+1.85 bps** | KHONG vuot |
| SOLUSDT | OFI_10s_z h=180s | **+1.79 bps** | KHONG vuot |
| BNBUSDT | MICROGAP h=60s | **+2.80 bps** | KHONG vuot |

Dien giai:
- Tin hieu CO cau truc du doan that (decile monotic, train->holdout cung huong) — day la alpha vi co structure co that.
- Nhung bien do chi 1-3 bps, trong khi taker phai tra 6 bps roundtrip => **NO-GO cho moi chien luoc taker**.
- Don ma hoa tin hieu 1-3 bps chi con duong maker (GTX) — ma maker da bi SignalLab pha san: fill chi 3-7%, adverse selection, net -8bps tren 2550 probe.

## 6. Ket luan tong sau T5 — tat ca huong da co bang chung du lieu that

| Huong | Bang chung | Trang thai |
|---|---|---|
| Scalping taker | Edge L2 1-3 bps < phi 6 bps (T5) | **NO-GO vinh vien** |
| Scalping maker | SignalLab 2550 probe net -8bps, fill 3-7% | **NO-GO** |
| Carry funding | 1/900 coin dat chuan; print that dau tien net -367bps (gia -521 vs funding +160) | **NO-GO hien tai**, xem lai theo dieu kien QUYET_DINH |
| Listing/flip, spot-perp | Vi pham R1 / thieu spot | NO-GO |

He thong o che do DO LUONG. Khong co edge nao duoc chung minh. Bat cu huong moi deu can bang chung du lieu, khong bang ky vong.
