#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo; echo "安装没有完成。请把以上报错截图保存，查看 docs/manual.html 的排错章节。"; read -r -p "按回车关闭…"' ERR
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python。请安装 python.org 的 Python 3.12 或 3.13 macOS universal2 版本。"
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,11), "需要 Python 3.11 或更新版本，推荐 3.12/3.13"'
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python - <<'PY'
import hashlib
from pathlib import Path
Path('.venv/setup.sha256').write_text(hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest())
PY
echo
echo "安装完成！双击“启动工作台.command”。中文说明书在 docs/manual.html。"
read -r -p "按回车关闭…"
