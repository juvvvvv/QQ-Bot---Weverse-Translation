@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto directory_error

set "VENV_PYTHON=.venv\Scripts\python.exe"
if not exist "run.py" goto missing_files
if not exist "requirements.txt" goto missing_files
if not exist "%VENV_PYTHON%" goto needs_setup
"%VENV_PYTHON%" -c "import sys, hashlib; from pathlib import Path; assert sys.version_info >= (3, 11) and sys.platform == 'win32'; marker=Path('.venv/setup.sha256'); assert marker.exists() and marker.read_text(encoding='utf-8') == hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest(); import fastapi, uvicorn, PIL, websockets, playwright, httpx, python_multipart"
if errorlevel 1 goto needs_setup

rem The old version checks DISPLAY to detect a desktop outside macOS.
rem This process-local flag lets that check accept Windows; Chromium uses Windows normally.
if not defined DISPLAY set "DISPLAY=windows"

rem Supply a Windows Chinese font without changing source code or saved settings.
if not defined WEVERSE_FONT if exist "%WINDIR%\Fonts\msyh.ttc" set "WEVERSE_FONT=%WINDIR%\Fonts\msyh.ttc"
if not defined WEVERSE_FONT if exist "%WINDIR%\Fonts\simhei.ttf" set "WEVERSE_FONT=%WINDIR%\Fonts\simhei.ttf"
if not defined WEVERSE_FONT if exist "%WINDIR%\Fonts\simsun.ttc" set "WEVERSE_FONT=%WINDIR%\Fonts\simsun.ttc"

"%VENV_PYTHON%" -c "import socket, sys; s=socket.socket(); s.settimeout(1); result=s.connect_ex(('127.0.0.1', 8800)); s.close(); sys.exit(0 if result == 0 else 1)" >nul 2>&1
if not errorlevel 1 goto port_busy

rem Open the workbench only after its local HTTP server responds.
set "BROWSER_PYTHON=%VENV_PYTHON%"
if exist ".venv\Scripts\pythonw.exe" set "BROWSER_PYTHON=.venv\Scripts\pythonw.exe"
start "" /b "%BROWSER_PYTHON%" -c "exec('import time, urllib.request, webbrowser\nopener = urllib.request.build_opener(urllib.request.ProxyHandler({}))\nfor _ in range(40):\n    try:\n        with opener.open(\'http://127.0.0.1:8800/\', timeout=1) as response:\n            if response.status == 200:\n                webbrowser.open(\'http://127.0.0.1:8800/\')\n                break\n    except Exception:\n        time.sleep(0.5)')"

echo 工作台地址：http://127.0.0.1:8800
echo 请保持本窗口打开，并保持电脑运行。关闭工作台时按 Ctrl+C。
"%VENV_PYTHON%" run.py
set "RUN_EXIT=%ERRORLEVEL%"
if not "%RUN_EXIT%"=="0" echo 工作台已退出。如果上方有报错，请保留完整报错。
popd
pause
exit /b %RUN_EXIT%

:missing_files
echo 请先完整解压原版项目，再将两个 bat 文件放在与 run.py、requirements.txt 同一层。
goto failed

:needs_setup
echo 请先双击“首次安装.bat”，完成 Windows 依赖安装或更新后再启动。
goto failed

:port_busy
echo 8800 端口正在使用。若工作台已经启动，请打开 http://127.0.0.1:8800
echo 若要重新启动，请先关闭之前的工作台，再运行本文件。
goto failed

:directory_error
echo 无法进入脚本所在文件夹，请把完整项目放到本机文件夹后重试。
pause
exit /b 1

:failed
popd
pause
exit /b 1
