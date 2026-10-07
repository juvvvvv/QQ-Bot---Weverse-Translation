#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo; echo "安装没有完成。请把以上报错截图保存，查看 docs/manual.html 的排错章节。"; read -r -p "按回车关闭…"' ERR
QQBOT_PYTHON=""
for candidate in python3.13 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 /usr/local/bin/python3.13 /opt/homebrew/bin/python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3,11)' >/dev/null 2>&1; then
    QQBOT_PYTHON="$candidate"
    break
  fi
done
if [ -z "$QQBOT_PYTHON" ]; then
  echo "未找到可用 Python。请安装 python.org 的 Python 3.13 macOS universal2 版本（Intel Mac 也适用）。"
  exit 1
fi
echo "Mac 推荐 Python 3.13；本次使用："
"$QQBOT_PYTHON" --version
if [ ! -x .venv/bin/python ]; then "$QQBOT_PYTHON" -m venv .venv; fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3,11), "现有 .venv 的 Python 太旧，请将 .venv 改名后重新安装，保留 data。"'
.venv/bin/python -c 'from pathlib import Path; Path(".venv/setup.sha256").unlink(missing_ok=True)'
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
