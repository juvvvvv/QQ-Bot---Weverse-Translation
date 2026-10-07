@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto directory_error

echo 正在安装 Windows 版工作台，首次安装需要联网。
if not exist "requirements.txt" goto missing_files
if not exist "run.py" goto missing_files

set "VENV_PYTHON=.venv\Scripts\python.exe"
if exist "%VENV_PYTHON%" goto check_existing_venv

set "PYTHON_CMD="
py -3.13 -c "import sys; assert sys.version_info >= (3, 11) and sys.platform == 'win32'" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.13"
if defined PYTHON_CMD goto create_venv
py -3.12 -c "import sys; assert sys.version_info >= (3, 11) and sys.platform == 'win32'" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.12"
if defined PYTHON_CMD goto create_venv
py -3.11 -c "import sys; assert sys.version_info >= (3, 11) and sys.platform == 'win32'" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.11"
if defined PYTHON_CMD goto create_venv
py -3 -c "import sys; assert sys.version_info >= (3, 11) and sys.platform == 'win32'" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if defined PYTHON_CMD goto create_venv
python -c "import sys; assert sys.version_info >= (3, 11) and sys.platform == 'win32'" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD goto missing_python

:create_venv
%PYTHON_CMD% -m venv ".venv"
if errorlevel 1 goto install_error

:check_existing_venv
"%VENV_PYTHON%" -c "import sys; assert sys.version_info >= (3, 11) and sys.platform == 'win32'"
if errorlevel 1 goto broken_venv

rem Invalidate the readiness marker until every installation step succeeds.
"%VENV_PYTHON%" -c "from pathlib import Path; Path('.venv/setup.sha256').unlink(missing_ok=True)"
if errorlevel 1 goto install_error
"%VENV_PYTHON%" -m pip install -r "requirements.txt"
if errorlevel 1 goto install_error
"%VENV_PYTHON%" -m playwright install chromium
if errorlevel 1 goto install_error
"%VENV_PYTHON%" -c "import hashlib; from pathlib import Path; Path('.venv/setup.sha256').write_text(hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest(), encoding='utf-8')"
if errorlevel 1 goto install_error

echo.
echo 安装完成！请双击“启动工作台.bat”。
echo 中文说明书位于 docs\manual.html，其中 Mac 启动步骤请改用这两个 bat 文件。
popd
pause
exit /b 0

:missing_files
echo 缺少 requirements.txt 或 run.py，请先完整解压原版项目。
echo 请把这两个 bat 文件放在与 requirements.txt、run.py 同一层的文件夹内。
goto failed

:missing_python
echo 未找到 Windows Python 3.11 或更新版本，推荐安装官方 Python 3.13 64 位版。
echo 下载地址：https://www.python.org/downloads/windows/
echo 安装时勾选 Add python.exe to PATH，安装后重新双击本文件。
goto failed

:broken_venv
echo 现有 .venv 无法运行，或 Python 版本不符合要求。
echo 请关闭工作台，将 .venv 文件夹改名为 .venv-old 后重新运行本文件。
echo data 文件夹保存设置与图片，请保留。
goto failed

:install_error
echo.
echo 安装没有完成。请保留以上完整报错，检查网络与 Python 安装。
goto failed

:directory_error
echo 无法进入脚本所在文件夹，请把完整项目放到本机文件夹后重试。
pause
exit /b 1

:failed
popd
pause
exit /b 1
