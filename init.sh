#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python -m pytest -q
"F:/AI_Agent_Toolkit/env/Scripts/python.exe" -m pytest "F:/AI_Agent_Toolkit/tools" -q
