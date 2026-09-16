"""One-click Web UI Observatory Controller for Windows / Multi-platform.

Commands:
  python tools/ui_control.py start   -> Starts background UI server & opens browser
  python tools/ui_control.py stop    -> Gracefully stops UI server
  python tools/ui_control.py status  -> Checks if UI server is running
"""
import os
import sys
import time
import json
import socket
import subprocess
import webbrowser
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / 'data' / 'ui_server.pid'
PORT = 8765
URL = f'http://127.0.0.1:{PORT}'


def is_port_in_use(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def get_pid_from_port(port: int = PORT) -> list:
    pids = []
    if os.name == 'nt':
        try:
            out = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode('utf-8', errors='ignore')
            for line in out.strip().splitlines():
                if 'LISTENING' in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pids.append(int(parts[-1]))
        except Exception:
            pass
    return list(set(pids))


def start():
    print('=' * 70)
    print('  KHOI DONG GIAO DIEN WEB OBSERVATORY (REAL-TIME)')
    print('=' * 70)

    if is_port_in_use(PORT):
        print(f'[THONG BAO] Giao dien da dang chay san tai: {URL}')
    else:
        print(f'[1/3] Dang khoi dong Web Server tren cong {PORT}...')
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        py_exe = sys.executable
        if os.name == 'nt':
            CREATE_NEW_CONSOLE = 0x00000010
            proc = subprocess.Popen(
                [py_exe, str(ROOT / 'core' / 'web_ui_server.py'), '--port', str(PORT)],
                cwd=str(ROOT),
                creationflags=CREATE_NEW_CONSOLE
            )
        else:
            proc = subprocess.Popen(
                [py_exe, str(ROOT / 'core' / 'web_ui_server.py'), '--port', str(PORT)],
                cwd=str(ROOT),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        PID_FILE.write_text(str(proc.pid))
        print(f'[2/3] Da khoi tao tien trinh (PID: {proc.pid}). Cho ket noi...')

        # Wait up to 5s for port to open
        ready = False
        for _ in range(10):
            time.sleep(0.5)
            if is_port_in_use(PORT):
                ready = True
                break

        if not ready:
            print('[LOI] Khong the khoi dong Web Server. Vui long thu lai.')
            return 1

    print(f'[3/3] Dang tu dong mo trinh duyet den: {URL}')
    webbrowser.open(URL)

    print('=' * 70)
    print(f'  [THANH CONG] GIAO DIEN REAL-TIME DANG CHAY!')
    print(f'  Dia chi truy cap: {URL}')
    print(f'  De tat giao dien, chay file: TAT_GIAO_DIEN.bat')
    print('=' * 70)
    return 0


def stop():
    print('=' * 70)
    print('  TAT GIAO DIEN WEB OBSERVATORY')
    print('=' * 70)

    pids = get_pid_from_port(PORT)
    if PID_FILE.exists():
        try:
            saved_pid = int(PID_FILE.read_text().strip())
            pids.append(saved_pid)
        except Exception:
            pass

    pids = list(set(pids))

    if not pids and not is_port_in_use(PORT):
        print(f'[THONG BAO] Giao dien da duoc tat truoc do (Cong {PORT} khong hoat dong).')
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
        return 0

    print(f'[INFO] Dang tat tien trinh Web Server (PIDs: {pids})...')
    for pid in pids:
        try:
            if os.name == 'nt':
                subprocess.call(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, 9)
        except Exception:
            pass

    time.sleep(1.0)
    PID_FILE.unlink(missing_ok=True)

    if not is_port_in_use(PORT):
        print('=' * 70)
        print(f'  [THANH CONG] DA TAT GIAO DIEN! (Cong {PORT} da giai phong)')
        print('=' * 70)
        return 0
    else:
        print('[CANH BAO] Cong 8765 van dang mo. Vui long kiem tra lai.')
        return 1


def status():
    running = is_port_in_use(PORT)
    pids = get_pid_from_port(PORT)
    print('=' * 70)
    print(f'  TRANG THAI GIAO DIEN: {"DANG CHAY (ONLINE)" if running else "DANG TAT (OFFLINE)"}')
    print(f'  URL: {URL}')
    if running:
        print(f'  PID: {pids}')
    print('=' * 70)
    return 0 if running else 1


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if cmd == 'start':
        sys.exit(start())
    elif cmd == 'stop':
        sys.exit(stop())
    elif cmd == 'status':
        sys.exit(status())
    else:
        print(f'Lenh khong hop le: {cmd}. Dung: start, stop, status')
        sys.exit(1)
