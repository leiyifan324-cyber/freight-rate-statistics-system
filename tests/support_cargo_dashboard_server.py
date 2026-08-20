import json
import os
import signal
import sys
import tempfile
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from freight_runtime import (  # noqa: E402
    FreightConfigurationManager,
    RuntimeStatus,
    start_status_dashboard,
)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    port = int(os.environ.get("CARGO_UI_TEST_PORT", "8877"))
    with tempfile.TemporaryDirectory(prefix="freight-cargo-ui-") as test_root:
        live_path = os.path.join(test_root, "qq_live_config.json")
        rules_path = os.path.join(test_root, "freight_rules.json")
        write_json(live_path, {
            "ws_url": "ws://127.0.0.1:3001",
            "group_ids": ["123456"],
            "group_names": {"123456": "页面测试群"},
            "group_default_origins": {"123456": "南宁"},
        })
        write_json(rules_path, {
            "default_origin": "南宁",
            "origin_groups": {"南宁": ["南宁"], "贵港": ["贵港"]},
            "destination_groups": {"佛山": ["佛山"], "杭州": ["杭州"]},
            "board_types": ["大板", "红板", "拼板"],
            "default_cargo_subcategory": "大板",
            "default_cargo_category": "板材",
            "cargo_types": [
                {"name": "大板", "aliases": ["大板"], "category": ""},
                {"name": "红板", "aliases": ["红板"], "category": ""},
                {"name": "拼板", "aliases": ["拼板"], "category": ""},
            ],
            "cargo_route_scopes": [],
            "price_threshold": 1000,
        })
        manager = FreightConfigurationManager(live_path, rules_path)
        status = RuntimeStatus(os.path.join(test_root, "status"))
        server = start_status_dashboard(
            status,
            "127.0.0.1",
            port,
            configuration=manager,
        )
        if server is None:
            raise RuntimeError("测试管理页面启动失败")

        stopped = False

        def stop(_signum=None, _frame=None):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop)
        restart_seen_at = None
        try:
            while not stopped:
                if manager.restart_pending():
                    restart_seen_at = restart_seen_at or time.monotonic()
                    if time.monotonic() - restart_seen_at > 1.0:
                        break
                time.sleep(0.1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
