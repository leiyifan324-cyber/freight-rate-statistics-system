import json
import os
import sys
import tempfile
import time
import urllib.request


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


def request_json(url, method="GET", payload=None, origin=None, csrf=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    if csrf:
        headers["X-Freight-CSRF"] = csrf
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main():
    with tempfile.TemporaryDirectory(prefix="freight-dashboard-test-") as directory:
        live_path = os.path.join(directory, "qq_live_config.json")
        rules_path = os.path.join(directory, "freight_rules.json")
        write_json(live_path, {
            "ws_url": "ws://127.0.0.1:3001",
            "access_token": "",
            "napcat_launcher": "",
            "group_ids": ["123456789"],
            "group_names": {"123456789": "测试"},
            "group_default_origins": {"123456789": "南宁"},
        })
        write_json(rules_path, {
            "default_origin": "南宁",
            "origin_groups": {"南宁": ["南宁"]},
            "destination_groups": {"佛山": ["佛山", "南海"]},
            "board_types": ["大板"],
            "price_threshold": 1000,
        })
        restarts = []
        manager = FreightConfigurationManager(
            live_path,
            rules_path,
            restart_callback=lambda: restarts.append(True),
        )
        status = RuntimeStatus(os.path.join(directory, "output"))
        status.update(connection="connected")
        status.update_group(
            "123456789",
            name="测试",
            active_records=1,
            pending_records=0,
            rejected_records=0,
            last_event_at="2026-08-19T10:03:33+08:00",
            last_event_status="added",
            last_event_type="message_sent",
            last_event_reason="新增 1 条运价",
            last_message_id="self-1",
            queue_state="idle",
            queue_depth=0,
            excel_state="ready",
        )
        recent_payload = {
            "items": [{
                "group_name": "测试",
                "kind": "正式识别",
                "received_at": "2026-08-19T10:03:33+08:00",
                "effective_date": "2026-08-19",
                "sender": "测试发送者",
                "route": "南宁到佛山",
                "cargo": "大板",
                "price": "235",
                "detail": "南宁到佛山大板235",
            }],
        }
        server = start_status_dashboard(
            status,
            "127.0.0.1",
            0,
            configuration=manager,
            recent_data_provider=lambda _limit: recent_payload,
        )
        assert server is not None
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            started = time.perf_counter()
            with urllib.request.urlopen(base, timeout=5) as response:
                html = response.read().decode("utf-8")
                csrf = response.headers.get("X-Freight-CSRF-Token")
            page_ms = (time.perf_counter() - started) * 1000
            assert "setInterval(refreshAll,2000)" in html
            assert "last_event_status" in html
            assert "最近处理" in html
            assert "最近说明" in html
            assert "last_event_reason" in html
            assert "最近消息处理明细" in html
            assert "群队列/积压" in html

            started = time.perf_counter()
            code, api_status = request_json(base + "/api/status")
            status_ms = (time.perf_counter() - started) * 1000
            assert code == 200
            assert api_status["groups"]["123456789"]["last_event_type"] == "message_sent"
            assert api_status["groups"]["123456789"]["last_event_status"] == "added"
            assert api_status["groups"]["123456789"]["last_event_reason"] == "新增 1 条运价"

            code, recent = request_json(base + "/api/recent?limit=80")
            assert code == 200
            assert recent["items"][0]["kind"] == "正式识别"

            payload = {
                "version": manager.snapshot()["version"],
                "connection": {
                    "ws_url": "ws://127.0.0.1:3001",
                    "access_token": "",
                    "napcat_launcher": "",
                },
                "groups": [{
                    "id": "123456789",
                    "name": "测试",
                    "default_origin": "南宁",
                }],
                "origins": [{"name": "南宁", "aliases": ["南宁"]}],
                "destinations": [{"name": "佛山", "aliases": ["佛山", "南海"]}],
                "board_types": ["大板"],
                "price_threshold": "1000",
            }
            code, api_result = request_json(
                base + "/api/config",
                method="POST",
                payload=payload,
                origin=base,
                csrf=csrf,
            )
            assert code == 200 and api_result["ok"]
            deadline = time.time() + 4
            while time.time() < deadline and not restarts:
                time.sleep(0.05)
            assert restarts
        finally:
            server.shutdown()
            server.server_close()

    assert page_ms < 1000
    assert status_ms < 1000
    print(json.dumps({
        "page_response_ms_lt_1000": True,
        "status_api_response_ms_lt_1000": True,
        "poll_interval_ms": 2000,
        "event_fields_rendered": True,
        "event_reason_rendered": True,
        "recent_message_details_rendered": True,
        "config_post_and_restart_feedback": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
