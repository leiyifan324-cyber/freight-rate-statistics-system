"""Unified entry point for source runs and the Windows release executable."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser

import freight_supervisor
import qq_freight_parser


def application_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_parser(mode: str, config: str = "", data_dir: str = "") -> int:
    arguments = [sys.argv[0], "--mode", mode]
    if config:
        arguments.extend(["--config", config])
    if data_dir:
        arguments.extend(["--data-dir", data_dir])
    original = sys.argv
    try:
        sys.argv = arguments
        qq_freight_parser.main()
        return 0
    finally:
        sys.argv = original


def open_dashboard() -> int:
    webbrowser.open("http://127.0.0.1:8765/?view=config")
    return 0


def run_manual() -> int:
    data_dir = os.path.join(application_dir(), "手动文本统计")
    os.makedirs(data_dir, exist_ok=True)
    input_file = os.path.join(data_dir, "qq_chat.txt")
    if not os.path.exists(input_file):
        with open(input_file, "a", encoding="utf-8"):
            pass
    subprocess.Popen(["notepad.exe", input_file])
    return run_parser("file-watch", data_dir=data_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="物流运价统计系统")
    parser.add_argument(
        "--mode",
        choices=[
            "supervisor", "qq-live", "qq-groups", "manual", "file-once",
            "open-dashboard", "install-autostart", "uninstall-autostart", "shutdown",
        ],
        default="supervisor",
    )
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    config = args.config or os.path.join(application_dir(), "qq_live_config.json")

    if args.mode == "supervisor":
        return freight_supervisor.main()
    if args.mode == "qq-live":
        return run_parser("qq-live", config=config)
    if args.mode == "qq-groups":
        return run_parser("qq-groups", config=config)
    if args.mode == "manual":
        return run_manual()
    if args.mode == "file-once":
        return run_parser("file-once", data_dir=os.path.join(application_dir(), "手动文本统计"))
    if args.mode == "open-dashboard":
        return open_dashboard()
    if args.mode == "install-autostart":
        return freight_supervisor.install_autostart()
    if args.mode == "uninstall-autostart":
        return freight_supervisor.uninstall_autostart()
    if args.mode == "shutdown":
        return freight_supervisor.shutdown_other_instances()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
