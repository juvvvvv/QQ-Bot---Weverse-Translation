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
if exist ".venv" goto broken_venv

rem Confirm that Python really ran, rather than trusting a launcher's exit code.
set "QQBOT_PYTHON_PROBE=%TEMP%\qqbot-python-%RANDOM%-%RANDOM%.txt"
set "PYTHON_EXE="
call :probe_python py -3.14
if defined PYTHON_EXE goto create_venv
call :probe_python py -3.13
if defined PYTHON_EXE goto create_venv
call :probe_python py -3.12
if defined PYTHON_EXE goto create_venv
call :probe_python py -3.11
if defined PYTHON_EXE goto create_venv
call :probe_python py -3
if defined PYTHON_EXE goto create_venv
call :probe_python python
if defined PYTHON_EXE goto create_venv
goto missing_python

:create_venv
echo 已找到可用的 Python：
"%PYTHON_EXE%" --version
rem Run pip bootstrapping separately so its actual error is visible.
"%PYTHON_EXE%" -m venv --without-pip ".venv"
if not "%ERRORLEVEL%"=="0" goto install_error
if not exist "%VENV_PYTHON%" goto install_error

:check_existing_venv
"%VENV_PYTHON%" -c "import sys; assert sys.version_info >= (3, 11) and sys.platform == 'win32'"
if not "%ERRORLEVEL%"=="0" goto broken_venv

rem Invalidate the readiness marker until every installation step succeeds.
"%VENV_PYTHON%" -c "from pathlib import Path; Path('.venv/setup.sha256').unlink(missing_ok=True)"
if not "%ERRORLEVEL%"=="0" goto install_error

rem A failed earlier venv creation may have left Python but no pip.
"%VENV_PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 goto repair_pip
goto install_dependencies

:repair_pip
echo 正在初始化 pip，使用 Python 自带的安装包，此步骤不需要联网。
"%VENV_PYTHON%" -m ensurepip --upgrade --default-pip
if not "%ERRORLEVEL%"=="0" goto pip_error
"%VENV_PYTHON%" -m pip --version
if not "%ERRORLEVEL%"=="0" goto pip_error

:install_dependencies
echo 正在安装工作台依赖。
"%VENV_PYTHON%" -m pip install -r "requirements.txt"
if not "%ERRORLEVEL%"=="0" goto install_error
"%VENV_PYTHON%" -m playwright install chromium
if not "%ERRORLEVEL%"=="0" goto install_error
"%VENV_PYTHON%" -c "import hashlib; from pathlib import Path; Path('.venv/setup.sha256').write_text(hashlib.sha256(Path('requirements.txt').read_bytes()).hexdigest(), encoding='utf-8')"
if not "%ERRORLEVEL%"=="0" goto install_error

echo.
echo 安装完成！请双击“启动工作台.bat”。
echo 中文说明书位于 docs\manual.html，请阅读 Windows 安装步骤。
popd
pause
exit /b 0

:missing_files
echo 缺少 requirements.txt 或 run.py，请先完整解压原版项目。
echo 请把这两个 bat 文件放在与 requirements.txt、run.py 同一层的文件夹内。
goto failed

:missing_python
echo 未找到能运行的 Windows Python 3.11 或更新版本，Python 3.14 也符合版本要求。
echo 下载地址：https://www.python.org/downloads/windows/
echo 如果已经安装 Python，请保留当前版本，并运行 py -3.14 --version 和 python --version 检查。
goto failed

:broken_venv
echo 现有 .venv 不完整、来自其他系统，或引用了不可用的 Python。
echo 请关闭工作台，将 .venv 文件夹改名为 .venv-old 后重新运行本文件。
echo data 文件夹保存设置与图片，请保留。
goto failed

:install_error
echo.
echo 安装没有完成。请保留以上完整报错，检查网络与 Python 安装。
goto failed

:pip_error
echo.
echo Python 虚拟环境已创建，但 pip 初始化失败；上方是具体错误。
echo 这一步使用 Python 自带的文件，不是下载工作台依赖失败。
echo 请保留从“正在初始化 pip”开始的完整报错，再进一步排查。
echo 请勿删除 data 文件夹；旧项目能正常启动时，可在旧项目中同时替换 weverse_bot 和 static 文件夹。
goto failed

:directory_error
echo 无法进入脚本所在文件夹，请把完整项目放到本机文件夹后重试。
pause
exit /b 1

:failed
popd
pause
exit /b 1

:probe_python
set "PYTHON_EXE="
if exist "%QQBOT_PYTHON_PROBE%" del /q "%QQBOT_PYTHON_PROBE%" >nul 2>&1
%* -c "import os, sys; from pathlib import Path; assert sys.version_info >= (3, 11) and sys.platform == 'win32'; assert Path(sys.executable).is_file(); Path(os.environ['QQBOT_PYTHON_PROBE']).write_text(sys.executable, encoding='utf-8')" >nul 2>&1
if not exist "%QQBOT_PYTHON_PROBE%" exit /b 0
for /f "usebackq delims=" %%P in ("%QQBOT_PYTHON_PROBE%") do set "PYTHON_EXE=%%P"
del /q "%QQBOT_PYTHON_PROBE%" >nul 2>&1
if not defined PYTHON_EXE exit /b 0
if not exist "%PYTHON_EXE%" set "PYTHON_EXE="
exit /b 0
