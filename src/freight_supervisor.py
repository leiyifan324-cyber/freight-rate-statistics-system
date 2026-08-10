"""Windows-only supervisor for NapCatQQ and the freight collector."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time

from freight_runtime import CREATE_NO_WINDOW, NetworkClock, configure_logging


CREATE_NEW_CONSOLE = 0x00000010 if os.name == "nt" else 0
ERROR_ALREADY_EXISTS = 183
TASK_NAME = "FreightRateStatisticsSystemAutoStart"


def application_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE_DIR = application_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "qq_live_config.json")
LOG_DIR = os.path.join(BASE_DIR, "data", "logs")


def acquire_single_instance():
    if os.name != "nt":
        return object()
    handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Local\\FreightRateStatisticsSystemSupervisor"
    )
    if not handle or ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def process_name_running(image_name: str) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=10,
        creationflags=CREATE_NO_WINDOW,
    )
    return image_name.lower() in result.stdout.lower()


def collector_running() -> bool:
    executable = os.path.abspath(sys.executable).replace("'", "''")
    command = (
        "$found = Get-CimInstance Win32_Process | Where-Object { "
        f"$_.ExecutablePath -eq '{executable}' -and "
        "$_.CommandLine -like '*--mode qq-live*' } | Select-Object -First 1; "
        "if ($found) { exit 0 } else { exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        timeout=15,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def load_local_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_napcat_launcher(config: dict) -> str:
    path = os.path.expandvars(str(config.get("napcat_launcher", "")).strip())
    if path and not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return os.path.abspath(path) if path else ""


def launch_napcat(path: str) -> None:
    if path.lower().endswith((".bat", ".cmd")):
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/c", path]
    else:
        command = [path]
    subprocess.Popen(
        command,
        cwd=os.path.dirname(path),
        creationflags=CREATE_NEW_CONSOLE,
    )


def launch_collector() -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--mode", "qq-live", "--config", CONFIG_PATH]
    else:
        command = [
            sys.executable,
            os.path.join(BASE_DIR, "src", "freight_app.py"),
            "--mode",
            "qq-live",
            "--config",
            CONFIG_PATH,
        ]
    subprocess.Popen(
        command,
        cwd=BASE_DIR,
        creationflags=CREATE_NO_WINDOW,
    )


def install_autostart() -> int:
    if os.name != "nt":
        return 1
    if getattr(sys, "frozen", False):
        executable = sys.executable
        action = f'"{executable}" --mode supervisor'
    else:
        executable = sys.executable
        script = os.path.join(BASE_DIR, "src", "freight_app.py")
        action = f'"{executable}" "{script}" --mode supervisor'
    result = subprocess.run(
        [
            "schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON",
            "/TR", action, "/RL", "LIMITED", "/F",
        ],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode


def uninstall_autostart() -> int:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return 0 if result.returncode in {0, 1} else result.returncode


def shutdown_other_instances() -> int:
    if os.name != "nt":
        return 0
    executable = os.path.abspath(sys.executable).replace("'", "''")
    current_pid = os.getpid()
    command = (
        "Get-CimInstance Win32_Process | Where-Object { "
        f"$_.ExecutablePath -eq '{executable}' -and $_.ProcessId -ne {current_pid} "
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        timeout=20,
        creationflags=CREATE_NO_WINDOW,
    )
    return 0


def main() -> int:
    mutex_handle = acquire_single_instance()
    if mutex_handle is None:
        return 0

    logger = configure_logging(LOG_DIR, "freight-supervisor")
    clock = NetworkClock()
    config = load_local_config()
    logger.info("监督程序启动，联网校时结果: %s", clock.sync(config.get("time_check_urls")))
    last_napcat_launch = 0.0
    last_collector_launch = 0.0
    missing_launcher_logged = False

    while True:
        try:
            now = time.monotonic()
            config = load_local_config()
            napcat_running = process_name_running("NapCatWinBootMain.exe")
            qq_running = process_name_running("QQ.exe")
            napcat_launcher = resolve_napcat_launcher(config)
            if not napcat_running and not qq_running and now - last_napcat_launch > 60:
                if napcat_launcher and os.path.exists(napcat_launcher):
                    logger.warning("NapCat和QQ未运行，正在启动NapCat。")
                    launch_napcat(napcat_launcher)
                    last_napcat_launch = now
                    missing_launcher_logged = False
                elif not missing_launcher_logged:
                    logger.warning("尚未配置可用的NapCat启动器路径。")
                    missing_launcher_logged = True
            elif not napcat_running and qq_running:
                logger.warning("检测到普通QQ正在运行但NapCat未运行。")

            if not collector_running() and now - last_collector_launch > 30:
                logger.warning("实时采集器未运行，正在自动启动。")
                launch_collector()
                last_collector_launch = now
        except Exception:
            logger.exception("监督循环异常")
        time.sleep(15)


if __name__ == "__main__":
    raise SystemExit(main())
