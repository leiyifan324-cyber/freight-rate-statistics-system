import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "src", "freight_app.py")


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def main():
    with tempfile.TemporaryDirectory(
        prefix="freight-first-run-", ignore_cleanup_errors=True
    ) as directory:
        port = free_port()
        with open(os.path.join(ROOT, "config", "qq_live_config.example.json"), "r", encoding="utf-8") as stream:
            live = json.load(stream)
        with open(os.path.join(ROOT, "config", "freight_rules.default.json"), "r", encoding="utf-8") as stream:
            rules = json.load(stream)
        # Exercise an explicitly unconfigured install, independent of personal release defaults.
        live['group_ids'] = []
        live['group_names'] = {}
        live['group_default_origins'] = {}
        live['group_excluded_origins'] = {}
        live['group_data_lifecycle'] = {}
        live['ws_url'] = 'ws://127.0.0.1:9'
        live["output_root"] = os.path.join(directory, "data")
        live["rules_file"] = os.path.join(directory, "freight_rules.json")
        live["status_dashboard"]["port"] = port
        live["time_check_urls"] = ["https://www.microsoft.com"]
        config_path = os.path.join(directory, "qq_live_config.json")
        with open(config_path, "w", encoding="utf-8") as stream:
            json.dump(live, stream, ensure_ascii=False, indent=2)
        with open(os.path.join(directory, "freight_rules.json"), "w", encoding="utf-8") as stream:
            json.dump(rules, stream, ensure_ascii=False, indent=2)

        process = subprocess.Popen(
            [sys.executable, APP, "--mode", "qq-live", "--config", config_path],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 30
            status = None
            configuration = None
            while time.time() < deadline:
                if process.poll() is not None:
                    raise AssertionError("首次配置进程提前退出")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
                        status = json.loads(response.read().decode("utf-8"))
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2) as response:
                        configuration = json.loads(response.read().decode("utf-8"))
                    break
                except OSError:
                    time.sleep(0.5)
            assert status is not None and status["connection"] == "setup_required"
            assert configuration is not None and configuration["groups"] == []
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            time.sleep(0.5)

    print("FIRST_RUN_SETUP_OK")


if __name__ == "__main__":
    main()
