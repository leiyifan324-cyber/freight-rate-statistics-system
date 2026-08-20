@echo off
setlocal EnableExtensions
if /i "%~1"=="--check" (
    if not exist "%~dp0install_napcat.ps1" exit /b 1
    echo NAPCAT_BATCH_CHECK_OK
    exit /b 0
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_napcat.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] NapCatQQ installation failed.
)
pause
exit /b %EXIT_CODE%
