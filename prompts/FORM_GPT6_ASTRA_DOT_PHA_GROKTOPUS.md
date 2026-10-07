Role: World-Class Quantitative Systems Architect & Multi-Agent Swarm Researcher.
Format: Raw Markdown tables + mathematical formulas + surgical directives only. Zero conversational filler, zero throat-clearing, zero generic advice.

Input Context:
1. Workspace: F:\he_thong_da_tac_tu_2 (Python 3.12, 410 tests passing, Binance USD-M Futures, 8 agents: VESKA, NORO, LUMEN, TIDAL, ZEPHR, RUNE, OKAPI, MARIN; 76.8% execution consensus, 70.0% RUNE veto, HMAC-SHA256 TokenAuthority).
2. Hiện trạng: Hệ thống 1m scalping (DOGE Flush) đang rơi vào vòng lặp vô tận "sửa hệ thống -> cào dữ liệu -> lỗ/tê liệt":
   - Vốn 3.85 USDT, kill-switch cứng 0.05 USDT (equity floor 3.80 USDT).
   - Min notional Binance = 5 USDT (bắt buộc đòn bẩy ~1.3x - 2x).
   - Mỗi SL mất ~1.5 cent (phí taker + slippage chiếm tới 44% khoảng cách SL). Chỉ cần 3 SL liên tiếp là cháy ngân sách 5 cent.
   - Vừa siết rủi ro theo chuẩn GĐ2 (SL trần 20.82 bps) thì thị trường ATR 52 bps -> Engine tự BLOCK 100% lệnh vì SL thực tế vượt ngân sách (0.0153 > 0.0150 USDT). Mở lỏng thì chết, siết chặt thì tê liệt.
3. Tài liệu & Case Study bắt buộc tra cứu:
   - https://github.com/groktopus
   - https://x.com/silvanrec/status/2094848671544115555
   - Nội dung lan truyền:
     "GROKTOPUS: 8 AI AGENTS, 8 BỘ NÃO, 1 CƠ THỂ – CON QUÁI VẬT GIAO DỊCH BIẾN $150K THÀNH $198K CHỈ TRONG 1 BUỔI SÁNG!
     Hầu hết mọi người xây dựng bot theo mô hình: AI -> Ví -> Mua/Bán.
     Họ xây dựng một sàn giao dịch do 8 AI agent vận hành:
     - VESKA – Thực thi lệnh
     - NORO – Tính toán giá trị hợp lý
     - LUMEN – Phân tích tâm lý thị trường
     - TIDAL – Quét thị trường
     - ZEPHR – Phân tích thanh khoản
     - RUNE – Kiểm soát rủi ro
     - OKAPI – Quản lý phơi nhiễm
     - MARIN – Thanh lý giao dịch
     Mỗi agent có một nhiệm vụ riêng. Không agent nào tự ý di chuyển tiền.
     ĐỒNG THUẬN: Ngưỡng 76.8% mới giao dịch. Dưới 70%: RUNE từ chối.
     KẾT QUẢ: $150K -> $198K trong 1 buổi sáng (1.981 giao dịch trên 8 thị trường)."

Nhiệm vụ phân tích & nghiên cứu đột phá (Yêu cầu trả lời theo bảng và công thức toán vi mô):

Task 1: GIẢI MÃ SỰ THẬT GROKTOPUS (Truth vs Crypto Twitter Marketing)
- Phân tích case study silvanrec: 1.981 lệnh / buổi sáng trên $150k vốn là mô hình gì? (HFT Maker-Rebate, Cross-Exchange Latency Arbitrage, hay Pure Liquidity Provisioning?)
- Bóc trần sự khác biệt bản chất: Tại sao mô hình đó kiếm được tiền ở quy mô $150k (maker rebate VIP tier, đa thị trường, portfolio netting) nhưng sẽ TỬ NẠN 100% khi bê nguyên cấu trúc đó áp vào retail $3.85 USDT trên Binance Futures 1m scalping?

Task 2: ĐỐI SOÁT CÁC SÀN THAY THẾ CHO VỐN NHỎ
- So sánh Binance vs Bybit, Bitget, MEXC, BingX, Hyperliquid về: Min Notional, Min Contract Unit (DOGE, SOL, BTC), Taker/Maker Fee, API WebSocket Latency, và Cơ chế Isolated Margin cho tài khoản siêu nhỏ ($1 - $5).
- Nếu đổi sang sàn có min notional 0.5$ - 1$, hệ thống có thoát được bế tắc toán học hay không? Hay chỉ kéo dài thời gian trước khi chạm kill switch?

Task 3: CHIẾN LƯỢC ĐỘT PHÁ CẮT ĐỨT VÒNG LẶP "SỬA CODE - CÀO DATA - LỖ"
- Trình bày 3 hướng kiến trúc thoát hiểm khả thi nhất từ tài liệu học thuật và thực chiến thế giới (2024-2026):
  * Hướng 1: Funding Rate Arbitrage / Basis Trading (He-Manela-Ross 2024, Gupta-Polson 2026) — điều kiện vốn tối thiểu thực tế, cách tận dụng 8 agents.
  * Hướng 2: Cross-Sectional Momentum / Dispersion trên sàn có min notional nhỏ (TIDAL quét 50 coin, OKAPI chia rủi ro theo danh mục, thay vì all-in 1m DOGE).
  * Hướng 3: Pure Maker Avellaneda-Stoikov Microstructure (Post-Only GTX ăn rebate thay vì trả 5 bps taker khi stop out).
- Bảng ma trận so sánh 3 hướng: [Vốn tối thiểu | Tỷ lệ sống sót | Mức độ phụ thuộc phí | Độ phức tạp code | File cần sửa trong F:\he_thong_da_tac_tu_2].

Task 4: SPEC TRIỂN KHAI CHO CLAUDE / CODEX (Actionable Blueprint)
- Không chung chung. Đưa ra lộ trình dứt khoát 3 bước: Bước 1 đóng băng cái gì; Bước 2 chuyển hướng sang module nào có sẵn (`core/funding_carry.py` hay sàn khác); Bước 3 ngưỡng thống kê OOS để bấm nút live.
