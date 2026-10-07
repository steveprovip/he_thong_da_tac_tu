"""GROKTOPUS control panel — the single entry point.

Carry migration: default public Shadow and explicit Testnet pair execution.
Legacy paper measurement remains accessible. Mainnet requires the carry CLI's
two explicit arming arguments; there is no Mainnet menu entry.

Everything runs through sys.executable (absolute path), so no PATH, no
nested cmd, no start-quirks: the whole class of 'cannot find the file'
bugs from batch launchers is gone.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
L2_SYMBOLS = 'SOLUSDT,BTCUSDT,BNBUSDT,LINKUSDT'
L2_DEFAULT_HOURS = 1   # was 11: budgeted by research/l2_recorder.py --max-mb


def run_python(args, cwd=ROOT):
    return subprocess.call([sys.executable, *args], cwd=str(cwd))


def measure_paper() -> int:
    print('\n' + '=' * 78)
    print('  DO LUONG PAPER - mainnet public, KHONG dat lenh that')
    print('  Dashboard: http://127.0.0.1:8766 - Dung: Ctrl+C (tu dung sach)')
    print('=' * 78 + '\n')
    code = run_python(['scripts/run_concurrent_paper.py', '--market-data', 'mainnet', '--port', '8766'])
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


def carry(mode='shadow') -> int:
    if mode not in ('shadow', 'testnet'):
        raise ValueError('Launcher only supports Shadow and Testnet carry')
    print(f'\n[Carry] {mode.upper()} - REST 60s, isolated watchdog 30s, Ctrl+C closes both legs.\n')
    return run_python(['-m', 'core.runtime', '--carry', '--mode', mode])


def flush_doge() -> int:
    print('\n[DOGE 1m Flush] Chay Shadow giat vol 1m DOGEUSDT (Von 100k VND, Stop 5 cents)... Ctrl+C de dung.\n')
    return run_python(['-m', 'research.flush_shadow'])


def open_ui() -> int:
    print('\n[Web UI] Dang mo giao dien Real-time...')
    return run_python(['tools/ui_control.py', 'start'])


def close_ui() -> int:
    print('\n[Web UI] Dang tat giao dien Real-time...')
    return run_python(['tools/ui_control.py', 'stop'])


MENU = f"""
==============================================================================
      GROKTOPUS 2.0 - QUANT TRADING CONTROLLER
      Carry REST 60s / Margin watchdog 30s / Khong stream L2
      Mainnet khong co trong menu. Shadow khong dat lenh.
==============================================================================

  [1] [Carry] Shadow Spot-Perp Harvester (7-30 ngay)
  [2] [Carry] Testnet Harvester - can API key Spot + Futures testnet
  [3] LEGACY PAPER - do luong pipeline cu
  [4] [DOGE 1m] Shadow Flush Harvester (Von 100k VND / Phut-Gio / Stop 5 cents)
  
  [5] MO GIAO DIEN REAL-TIME (Browser http://127.0.0.1:8765)
  [6] TAT GIAO DIEN REAL-TIME

  [0] Thoat
==============================================================================
"""


def main() -> int:
    while True:
        print(MENU)
        choice = input('Nhap lua chon [1, 2, 3, 4, 5, 6, 0]: ').strip()
        if choice == '1':
            carry('shadow')
        elif choice == '2':
            carry('testnet')
        elif choice == '3':
            measure_paper()
        elif choice == '4':
            flush_doge()
        elif choice == '5':
            open_ui()
        elif choice == '6':
            close_ui()
        elif choice == '0':
            return 0
        else:
            print('\nLua chon khong hop le. Nhap 1, 2, 3, 4, 5, 6 hoac 0.\n')


if __name__ == '__main__':
    raise SystemExit(main())
