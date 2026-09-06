import subprocess
import sys
from config.settings import ROOT

CHOICES = {
    '1': ['run_concurrent_live.py', '--arm'],
    '2': ['run_binance_testnet.py', '--bootstrap'],
}

if __name__ == '__main__':
    print('==============================================================================')
    print('      GROKTOPUS 2.0 - BẢNG ĐIỀU KHIỂN HỆ THỐNG GIAO DỊCH ĐA TÁC TỬ')
    print('==============================================================================\n')
    print('  [1] CHẠY THỰC TẾ  - Giao dịch Mainnet trực tiếp (Khớp lệnh Post-Only GTX)')
    print('  [2] CHẠY GIẢ LẬP  - Giao dịch Testnet / Demo ($4,930.88 USDT ảo + Sổ lệnh thật)\n')
    print('  [0] Thoát\n')
    print('==============================================================================')
    choice = input('Nhập lựa chọn của bạn [1, 2, 0]: ').strip()
    if choice in CHOICES:
        raise SystemExit(subprocess.call([sys.executable, *CHOICES[choice]], cwd=ROOT))
