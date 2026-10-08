#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python - <<'PY'
import hashlib
from pathlib import Path
marker=Path('.venv/setup.sha256')
assert marker.exists() and marker.read_text()==hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest()
import fastapi, uvicorn, PIL, websockets, playwright
PY
then
  echo "请先运行“首次安装.command”，安装或更新依赖。"
  read -r -p "按回车关闭…"
  exit 1
fi
# Open the browser only after the local HTTP server responds; do not bind to LAN/public interfaces.
.venv/bin/python - <<'PY' &
import time, urllib.request, webbrowser
for _ in range(40):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8800/',timeout=1) as r:
            if r.status==200:
                webbrowser.open('http://127.0.0.1:8800/?fresh=' + str(time.time_ns()))
                break
    except Exception:
        time.sleep(.5)
PY
echo "关闭工作台：在此窗口按 Control+C。不要关闭窗口或让 Mac 休眠。"
exec .venv/bin/python run.py
