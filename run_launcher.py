import subprocess
import sys
from config.settings import ROOT

CHOICES = {'1': ['run_concurrent_paper.py'], '2': ['run_binance_testnet.py', '--bootstrap'],
           '3': ['run_concurrent_live.py'], '4': ['core/web_ui_server.py'],
           '5': ['tools/check_connection.py'], '6': ['tools/preflight_testnet.py'],
           '7': ['run_concurrent_paper.py', '--offline', '--seconds', '25']}

if __name__ == '__main__':
    print('GROKTOPUS\n1 Paper (real L2)\n2 Testnet (virtual orders)\n3 Mainnet advisory\n4 Dashboard preview\n5 Testnet connection/account check\n6 Offline pre-flight\n7 Offline synthetic paper run\n0 Exit')
    choice = input('Select: ').strip()
    if choice in CHOICES:
        raise SystemExit(subprocess.call([sys.executable, *CHOICES[choice]], cwd=ROOT))
