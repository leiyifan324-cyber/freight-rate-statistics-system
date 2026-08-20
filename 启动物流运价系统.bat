@echo off
setlocal EnableExtensions
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo [Freight System] Checking runtime...

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    pause
    exit /b 1
)

if not exist "src\freight_app.py" (
    echo [ERROR] This launcher is not in a complete project directory.
    pause
    exit /b 1
)

if not exist "qq_live_config.json" (
    if not exist "config\qq_live_config.example.json" (
        echo [ERROR] Missing config\qq_live_config.example.json.
        pause
        exit /b 1
    )
    copy /y "config\qq_live_config.example.json" "qq_live_config.json" >nul
    echo [OK] Created qq_live_config.json.
)

if not exist "freight_rules.json" (
    if not exist "config\freight_rules.default.json" (
        echo [ERROR] Missing config\freight_rules.default.json.
        pause
        exit /b 1
    )
    copy /y "config\freight_rules.default.json" "freight_rules.json" >nul
    echo [OK] Created freight_rules.json.
)

python -c "import pandas, matplotlib, openpyxl, websocket, lunardate" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Required Python packages are missing. Run:
    echo python -m pip install -r requirements.txt
    pause
    exit /b 1
)

if /i "%~1"=="--check" (
    python -m py_compile "src\freight_app.py" "src\freight_supervisor.py" "src\freight_runtime.py" "src\qq_freight_parser.py"
    if errorlevel 1 (
        echo [ERROR] Python source validation failed.
        exit /b 1
    )
    echo [PASS] Launcher, config, dependencies and Python sources are ready.
    exit /b 0
)

echo [Freight System] Starting. The dashboard will open when ready...
python "src\freight_app.py" --mode start --config "qq_live_config.json"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] The system did not become ready within 90 seconds.
    echo Check NapCat, ports 3001/8765, and data\logs.
    pause
)

exit /b %EXIT_CODE%
