@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 备用启动方式：如果双击 .pyw 没有反应，就用这个 bat。
rem 它会优先用 pythonw 静默启动，失败则回退到 python 并保留窗口显示报错。

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "WorkBuddy悬浮窗.pyw"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "WorkBuddy悬浮窗.pyw"
    pause
    exit /b 0
)

echo 未找到 Python，请确认已安装 Python 3 并加入 PATH。
pause
