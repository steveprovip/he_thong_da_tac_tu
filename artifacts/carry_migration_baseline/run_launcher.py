"""GROKTOPUS control panel — the single entry point.

Policy 09/09/2026: scalping is KILLED (net -6.72 USDT / edge -12.6 bps over
102 verified trips). This launcher offers MEASUREMENT only: paper runs, L2
recording, and the testnet kill switch. Live mainnet and testnet scalping
sessions are intentionally NOT offered — re-enabling them is a code change,
not a menu click.

Everything runs through sys.executable (absolute path), so no PATH, no
nested cmd, no start-quirks: the whole class of 'cannot find the file'
bugs from batch launchers is gone.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
L2_SYMBOLS = 'SOLUSDT,BTCUSDT,BNBUSDT,LINKUSDT'
L2_DEFAULT_HOURS = 11


def run_python(args, cwd=ROOT):
    return subprocess.call([sys.executable, *args], cwd=str(cwd))


def measure_paper() -> int:
    print('\n' + '=' * 78)
    print('  DO LUONG PAPER - mainnet public, KHONG dat lenh that')
    print('  Dashboard: http://127.0.0.1:8766 - Dung: Ctrl+C (tu dung sach)')
    print('=' * 78 + '\n')
    code = run_python(['run_concurrent_paper.py', '--market-data', 'mainnet', '--port', '8766'])
    print('\n[audit] Doi soat sau phien...')
    run_python(['tools/audit_execution.py'])
    print('\n[P1-P3] Cham diem gop...')
    run_python(['phan_tich/pool_phien.py', '--since', '0'])
    return code


def record_l2() -> int:
    print('\n' + '=' * 78)
    print('  GHI DU LIEU L2 THAT - mainnet public WS, khong key, khong lenh')
    print('  bookTicker + depth@100ms + local-timestamp -> data/l2/<symbol>/')
    print('  Chay doc lap; co the chay dong luc paper. Ctrl+C de dung.')
    print('=' * 78 + '\n')
    raw = input(f'Thoi luong gio [Enter = {L2_DEFAULT_HOURS}]: ').strip()
    try:
        hours = float(raw) if raw else float(L2_DEFAULT_HOURS)
    except ValueError:
        hours = float(L2_DEFAULT_HOURS)
    seconds = max(60, int(hours * 3600))
    print(f'\nBat dau ghi {hours}h ({seconds}s). Dung = Ctrl+C.\n')
    return run_python(['research/l2_recorder.py', '--symbols', L2_SYMBOLS, '--seconds', str(seconds)])


def kill_switch() -> int:
    print('\nTat khan testnet (huy lenh + dong vi the von ao)...\n')
    return run_python(['tools/shutdown_system.py', '--mode', 'testnet'])


MENU = f"""
==============================================================================
      GROKTOPUS 2.0 - DO LUONG PAPER + L2 (LIVE KHOA 09/09/2026)
      Scalping KILL: net -6.72 / edge -12.6 bps tren 102 trips da xac minh.
      Mo lai live la thay doi CODE, khong phai lua chon menu.
==============================================================================

  [1] DO LUONG PAPER - mainnet public, khong lenh that, tu audit sau phien
  [2] GHI L2 THAT    - {L2_DEFAULT_HOURS}h mac dinh, de backtest queue/latency
  [3] TAT KHAN       - huy lenh + dong vi the testnet

  [0] Thoat
==============================================================================
"""


def main() -> int:
    while True:
        print(MENU)
        choice = input('Nhap lua chon [1, 2, 3, 0]: ').strip()
        if choice == '1':
            measure_paper()
        elif choice == '2':
            record_l2()
        elif choice == '3':
            kill_switch()
        elif choice == '0':
            return 0
        else:
            print('\nLua chon khong hop le. Nhap 1, 2, 3 hoac 0.\n')


if __name__ == '__main__':
    raise SystemExit(main())

