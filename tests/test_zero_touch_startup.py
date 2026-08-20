import json
import os
import socket
import sys
import tempfile
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import freight_supervisor
from freight_runtime import RuntimeStatus, start_status_dashboard


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def main():
    with tempfile.TemporaryDirectory(
        prefix="freight-zero-touch-", ignore_cleanup_errors=True
    ) as directory:
        port = free_port()
        status = RuntimeStatus(os.path.join(directory, "data"))
        status.update(connection="connected")
        server = start_status_dashboard(status, "127.0.0.1", port)
        assert server is not None
        config_path = os.path.join(directory, "qq_live_config.json")
        with open(config_path, "w", encoding="utf-8") as stream:
            json.dump({
                "status_dashboard": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": port,
                }
            }, stream)

        try:
            base_url = freight_supervisor.wait_for_dashboard(
                config_path=config_path,
                timeout_seconds=3,
            )
            assert base_url == f"http://127.0.0.1:{port}"
            with urllib.request.urlopen(base_url + "/api/health", timeout=2) as response:
                health = json.loads(response.read().decode("utf-8"))
            assert health["ok"] is True
            assert health["service"] == "freight-collector"
            assert health["connection"] == "connected"

            opened = []
            assert freight_supervisor.open_dashboard_when_ready(
                config_path=config_path,
                view="status",
                timeout_seconds=3,
                browser_opener=opened.append,
            )
            assert opened == [base_url + "/"]
            assert freight_supervisor.open_dashboard_when_ready(
                config_path=config_path,
                view="auto",
                timeout_seconds=3,
                browser_opener=opened.append,
            )
            assert opened[-1] == base_url + "/"
            assert freight_supervisor.open_dashboard_when_ready(
                config_path=config_path,
                view="config",
                timeout_seconds=3,
                browser_opener=opened.append,
            )
            assert opened[-1] == base_url + "/?view=config"

            status.update(connection="setup_required")
            assert freight_supervisor.open_dashboard_when_ready(
                config_path=config_path,
                view="auto",
                timeout_seconds=3,
                browser_opener=opened.append,
            )
            assert opened[-1] == base_url + "/?view=config"
        finally:
            server.shutdown()
            server.server_close()
            status.close()

    with open(os.path.join(ROOT, "installer", "assets", "启动系统.bat"), "r", encoding="utf-8") as stream:
        launcher = stream.read()
    assert "--mode start" in launcher
    assert "timeout /t" not in launcher.lower()
    assert "http://127.0.0.1:8765" not in launcher

    print(json.dumps({
        "health_endpoint": True,
        "dynamic_dashboard_port": True,
        "wait_until_ready": True,
        "status_page_auto_open": True,
        "config_page_auto_open": True,
        "first_run_opens_configuration": True,
        "fixed_delay_removed": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
