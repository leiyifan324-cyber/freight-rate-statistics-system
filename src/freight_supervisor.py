"""Windows-only supervisor for NapCatQQ and the freight collector."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Callable

from freight_runtime import (
    CREATE_NO_WINDOW,
    NetworkClock,
    configure_logging,
    validate_dashboard_host,
    validate_tcp_port,
)


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


def _powershell_quote(value: str) -> str:
    return str(value).replace("'", "''")


def build_managed_process_filter(
    current_pid: int = 0,
    frozen: bool | None = None,
    modes: tuple[str, ...] = ("supervisor", "qq-live"),
) -> str:
    """构造只匹配本项目实例的PowerShell条件，禁止误杀通用Python。"""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    mode_filter = " -or ".join(
        f"$_.CommandLine -like '*--mode {str(mode)}*'"
        for mode in modes
    )
    if is_frozen:
        executable = _powershell_quote(os.path.abspath(sys.executable))
        identity_filter = f"$_.ExecutablePath -eq '{executable}'"
    else:
        executable = _powershell_quote(os.path.abspath(sys.executable))
        app_path = _powershell_quote(os.path.join(BASE_DIR, "src", "freight_app.py"))
        base_dir = _powershell_quote(BASE_DIR)
        identity_filter = (
            f"$_.ExecutablePath -eq '{executable}' -and "
            f"$_.CommandLine -like '*{app_path}*' -and "
            f"$_.CommandLine -like '*{base_dir}*'"
        )
    pid_filter = f"$_.ProcessId -ne {int(current_pid)}" if current_pid else "$true"
    return f"({identity_filter}) -and ({mode_filter}) -and ({pid_filter})"


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
    process_filter = build_managed_process_filter(
        current_pid=os.getpid(),
        modes=("qq-live",),
    )
    command = (
        "$found = Get-CimInstance Win32_Process | Where-Object { "
        f"{process_filter} "
        "} | Select-Object -First 1; "
        "if ($found) { exit 0 } else { exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        timeout=15,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def load_local_config(config_path: str = CONFIG_PATH) -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def dashboard_base_url(config: dict) -> str:
    dashboard = config.get("status_dashboard", {})
    if not isinstance(dashboard, dict) or not dashboard.get("enabled", True):
        raise ValueError("管理页面未启用。")
    host = validate_dashboard_host(dashboard.get("host", "127.0.0.1"))
    port = validate_tcp_port(dashboard.get("port", 8765), "管理页面端口")
    return f"http://{host}:{port}"


def fetch_dashboard_health(
    config: dict,
    timeout_seconds: float = 2.0,
) -> dict | None:
    try:
        request = urllib.request.Request(
            dashboard_base_url(config) + "/api/health",
            headers={"Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=max(0.1, timeout_seconds)) as response:
            if response.status != 200:
                return None
            value = json.loads(response.read().decode("utf-8"))
        if value.get("ok") is True and value.get("service") == "freight-collector":
            return value
        return None
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None


def dashboard_healthy(config: dict, timeout_seconds: float = 2.0) -> bool:
    return fetch_dashboard_health(config, timeout_seconds) is not None


def wait_for_dashboard(
    config_path: str = CONFIG_PATH,
    timeout_seconds: float = 90.0,
    poll_seconds: float = 0.25,
) -> str:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() <= deadline:
        config = load_local_config(config_path)
        if dashboard_healthy(config):
            return dashboard_base_url(config)
        time.sleep(max(0.05, poll_seconds))
    return ""


def open_dashboard_when_ready(
    config_path: str = CONFIG_PATH,
    view: str = "status",
    timeout_seconds: float = 90.0,
    browser_opener: Callable[[str], object] | None = None,
) -> bool:
    base_url = wait_for_dashboard(config_path, timeout_seconds)
    if not base_url:
        return False
    target_view = view
    if view == "auto":
        health = fetch_dashboard_health(load_local_config(config_path)) or {}
        target_view = "config" if health.get("configured") is False else "status"
    target = base_url + ("/?view=config" if target_view == "config" else "/")
    opener = browser_opener or webbrowser.open
    return bool(opener(target) is not False)


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


def launch_collector(config_path: str = CONFIG_PATH) -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--mode", "qq-live", "--config", config_path]
    else:
        command = [
            sys.executable,
            os.path.join(BASE_DIR, "src", "freight_app.py"),
            "--mode",
            "qq-live",
            "--config",
            config_path,
        ]
    subprocess.Popen(
        command,
        cwd=BASE_DIR,
        creationflags=CREATE_NO_WINDOW,
    )


def launch_supervisor(config_path: str = CONFIG_PATH) -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--mode", "supervisor", "--config", config_path]
    else:
        command = [
            sys.executable,
            os.path.join(BASE_DIR, "src", "freight_app.py"),
            "--mode",
            "supervisor",
            "--config",
            config_path,
        ]
    subprocess.Popen(
        command,
        cwd=BASE_DIR,
        creationflags=CREATE_NO_WINDOW,
    )


def start_system(config_path: str = CONFIG_PATH) -> int:
    """一次性入口：确保监督器运行，就绪后打开状态页或首次配置页。"""
    launch_supervisor(config_path)
    return 0 if open_dashboard_when_ready(config_path, view="auto") else 2


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
    current_pid = os.getpid()
    process_filter = build_managed_process_filter(current_pid=current_pid)
    command = (
        "Get-CimInstance Win32_Process | Where-Object { "
        f"{process_filter} "
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        timeout=20,
        creationflags=CREATE_NO_WINDOW,
    )
    return 0


def stop_managed_collectors() -> bool:
    """只终止本项目的实时采集子进程，供健康检查失效后自恢复。"""
    if os.name != "nt":
        return False
    process_filter = build_managed_process_filter(
        current_pid=os.getpid(),
        modes=("qq-live",),
    )
    command = (
        "$targets = Get-CimInstance Win32_Process | Where-Object { "
        f"{process_filter} "
        "}; $targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
        "if ($targets) { exit 0 } else { exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        timeout=20,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def main(config_path: str = CONFIG_PATH) -> int:
    mutex_handle = acquire_single_instance()
    if mutex_handle is None:
        return 0

    logger = configure_logging(LOG_DIR, "freight-supervisor")
    clock = NetworkClock()
    config = load_local_config(config_path)
    logger.info("监督程序启动，联网校时结果: %s", clock.sync(config.get("time_check_urls")))
    last_napcat_launch = 0.0
    last_collector_launch = 0.0
    missing_launcher_logged = False
    dashboard_was_healthy = False
    dashboard_failure_count = 0

    while True:
        try:
            now = time.monotonic()
            config = load_local_config(config_path)
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

            collector_is_running = collector_running()
            if not collector_is_running and now - last_collector_launch > 30:
                logger.warning("实时采集器未运行，正在自动启动。")
                launch_collector(config_path)
                last_collector_launch = now
                dashboard_failure_count = 0
            elif collector_is_running:
                healthy = dashboard_healthy(config)
                if healthy:
                    dashboard_was_healthy = True
                    dashboard_failure_count = 0
                elif dashboard_was_healthy:
                    dashboard_failure_count += 1
                    if dashboard_failure_count >= 6:
                        logger.error(
                            "采集器进程仍在但管理接口连续90秒无响应；"
                            "正在安全终止本项目采集器并自动恢复。"
                        )
                        stop_managed_collectors()
                        dashboard_failure_count = 0
                        dashboard_was_healthy = False
                        last_collector_launch = 0.0
        except Exception:
            logger.exception("监督循环异常")
        time.sleep(15)


if __name__ == "__main__":
    raise SystemExit(main())
