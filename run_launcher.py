import subprocess
import sys
from config.settings import ROOT

def run_mainnet_live() -> int:
    print('\n' + '=' * 78)
    print('  CẢNH BÁO AN TOÀN: BẠN ĐANG CHỌN CHẠY THỰC TẾ (MAINNET LIVE)')
    print('  * Tiền thật trên tài khoản Binance sẽ được sử dụng.')
    print('  * Khuyến nghị: Chỉ kích hoạt khi Signal Lab chứng minh có Edge dương.')
    print('=' * 78 + '\n')
    confirm = input("Nhập 'XAC NHAN' (hoặc 'CONFIRM') để mở khóa Mainnet: ").strip()
    if confirm.upper() not in ('XAC NHAN', 'CONFIRM'):
        print('\nĐã hủy kích hoạt Mainnet để bảo vệ an toàn tài khoản.')
        return 0
    return subprocess.call([sys.executable, 'run_concurrent_live.py', '--arm'], cwd=ROOT)

def run_testnet_demo() -> int:
    return subprocess.call([sys.executable, 'run_binance_testnet.py', '--bootstrap'], cwd=ROOT)

def run_signal_lab() -> int:
    print('\n--- [1/2] Signal Lab trên Testnet ---')
    subprocess.call([sys.executable, 'tools/calibrate_edge.py', '--mode', 'testnet'], cwd=ROOT)
    print('\n--- [2/2] Signal Lab trên Paper ---')
    subprocess.call([sys.executable, 'tools/calibrate_edge.py', '--mode', 'paper'], cwd=ROOT)
    return 0

def run_hft_backtest() -> int:
    print('\n--- HFT Backtest (Mô phỏng hàng đợi vi cấu trúc 90 phút) ---')
    subprocess.call([sys.executable, 'tools/run_backtest.py', '--synthetic', '--minutes', '90'], cwd=ROOT)
    return 0

if __name__ == '__main__':
    while True:
        print('==============================================================================')
        print('      GROKTOPUS 2.0 - BẢNG ĐIỀU KHIỂN HỆ THỐNG GIAO DỊCH ĐA TÁC TỬ')
        print('==============================================================================\n')
        print('  [1] CHẠY THỰC TẾ  - Giao dịch Mainnet trực tiếp (Có xác nhận an toàn 2 bước)')
        print('  [2] CHẠY GIẢ LẬP  - Giao dịch Testnet / Demo ($4,930.88 USDT ảo + Sổ lệnh thật)')
        print('  [3] SIGNAL LAB    - Đo lường Edge & Kiểm tra Alpha từ dữ liệu đã ghi')
        print('  [4] HFT BACKTEST  - Mô phỏng vi cấu trúc hàng đợi (hftbacktest 90 phút)\n')
        print('  [0] Thoát\n')
        print('==============================================================================')
        choice = input('Nhập lựa chọn của bạn [1, 2, 3, 4, 0]: ').strip()
        if choice == '1':
            raise SystemExit(run_mainnet_live())
        elif choice == '2':
            raise SystemExit(run_testnet_demo())
        elif choice == '3':
            run_signal_lab()
            print('\n' + '-' * 78)
            input('Nhấn Enter để quay lại menu...')
            print()
        elif choice == '4':
            run_hft_backtest()
            print('\n' + '-' * 78)
            input('Nhấn Enter để quay lại menu...')
            print()
        elif choice == '0':
            raise SystemExit(0)
        else:
            print('\nLựa chọn không hợp lệ. Vui lòng nhập 1, 2, 3, 4 hoặc 0.\n')

