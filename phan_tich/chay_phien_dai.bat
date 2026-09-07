@echo off
rem Phien dai do luong: 11 gio, cau hinh GIONG HET Phien 1 (chi doi --seconds).
rem Bat dau khuyen nghi 13:00-14:00 de don ca cua so 19:30-23:30 VN.
cd /d F:\he_thong_da_tac_tu_2

if not exist data\backup-truoc-phien-dai mkdir data\backup-truoc-phien-dai
copy /Y data\testnet-settled-v2.json data\backup-truoc-phien-dai\ >nul
copy /Y data\execution-audit.json data\backup-truoc-phien-dai\ >nul
copy /Y data\testnet-journal.sqlite data\backup-truoc-phien-dai\ >nul
echo Da backup 3 file vao data\backup-truoc-phien-dai\

echo Khoi dong phien dai 11 gio (39600s)...
python run_binance_testnet.py --bootstrap --bootstrap-fraction 0.01 --quote-ttl 8 --horizon 300 --seconds 39600 --mission-target 0 --max-atr-bps 20 --maker-exit-grace 8 --flatten-on-exit

echo.
echo Phien ket thuc (tu dong flatten). Chay:
echo   python tools\audit_execution.py
echo   python phan_tich\pool_phien.py --since 0
pause
