import json
import os
import sys
import tempfile
import copy
import urllib.error
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


def request_json(url, method="GET", payload=None, csrf=""):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Origin": url.split("/api/", 1)[0],
            "X-Freight-CSRF": csrf,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main():
    with tempfile.TemporaryDirectory() as directory:
        live_path = os.path.join(directory, "qq_live_config.json")
        rules_path = os.path.join(directory, "freight_rules.json")
        write_json(
            live_path,
            {
                "ws_url": "ws://127.0.0.1:3001",
                "group_ids": ["111111"],
                "group_names": {"111111": "测试群"},
                "group_default_origins": {"111111": "贵港"},
            },
        )
        write_json(
            rules_path,
            {
                "default_origin": "贵港",
                "origin_groups": {"贵港": ["贵港", "港北"]},
                "destination_groups": {"佛山": ["佛山", "南海"]},
                "board_types": ["大板"],
                "price_threshold": 1000,
            },
        )
        validations = []
        manager = FreightConfigurationManager(
            live_path,
            rules_path,
            validate_callback=lambda: validations.append(True),
        )
        initial = manager.snapshot()
        assert initial["groups"][0]["name"] == "测试群"
        assert initial["default_origin"] == "贵港"

        duplicate_alias = {
            **initial,
            "origins": [
                {"name": "贵港", "aliases": ["共同别名"]},
                {"name": "南宁", "aliases": ["共同别名"]},
            ],
        }
        try:
            manager.apply(duplicate_alias)
            raise AssertionError("重复别名本应被拒绝")
        except ValueError as exc:
            assert "同时属于" in str(exc)
        assert manager.snapshot() == initial

        expanded = {
            "connection": {
                "ws_url": "ws://127.0.0.1:3001",
                "access_token": "",
                "napcat_launcher": "",
            },
            "processing": {
                "reconnect_seconds": 2,
                "heartbeat_seconds": 10,
                "excel_batch_seconds": 0.2,
                "excel_full_refresh_seconds": 30,
            },
            "groups": [
                {"id": "111111", "name": "测试群", "default_origin": "贵港"},
                {"id": "222222", "name": "新群", "default_origin": "南宁"},
            ],
            "origins": [
                {"name": "贵港", "aliases": ["贵港", "港北"]},
                {"name": "南宁", "aliases": ["南宁", "武鸣"]},
            ],
            "destinations": [
                {"name": "佛山", "aliases": ["佛山", "南海"]},
                {"name": "上海", "aliases": ["上海", "嘉定"]},
            ],
            "default_origin": "南宁",
            "board_types": ["大板", "红板"],
            "price_threshold": "1200.5",
        }
        result = manager.apply(expanded)
        assert result["ok"] and os.path.isdir(result["backup_dir"])
        assert len(validations) == 1
        saved = manager.snapshot()
        assert len(saved["groups"]) == 2
        assert saved["price_threshold"] == 1200.5
        assert saved["default_origin"] == "南宁"
        assert saved["processing"]["heartbeat_seconds"] == 10
        assert saved["processing"]["excel_batch_seconds"] == 0.2

        modified = copy.deepcopy(expanded)
        modified["groups"][1]["name"] = "新群已修改"
        modified["price_threshold"] = "1100"
        modified["default_origin"] = "贵港"
        modified["processing"]["excel_full_refresh_seconds"] = 45
        result = manager.apply(modified)
        assert result["ok"]
        modified_saved = manager.snapshot()
        assert modified_saved["groups"][1]["name"] == "新群已修改"
        assert modified_saved["price_threshold"] == 1100
        assert modified_saved["default_origin"] == "贵港"
        assert modified_saved["processing"]["excel_full_refresh_seconds"] == 45

        invalid_processing = copy.deepcopy(modified)
        invalid_processing["processing"]["heartbeat_seconds"] = 2
        try:
            manager.apply(invalid_processing)
            raise AssertionError("过短心跳间隔本应被拒绝")
        except ValueError as exc:
            assert "心跳" in str(exc)
        assert manager.snapshot() == modified_saved

        invalid_manual_origin = copy.deepcopy(modified)
        invalid_manual_origin["default_origin"] = "不存在"
        try:
            manager.apply(invalid_manual_origin)
            raise AssertionError("不存在的手动默认始发地本应被拒绝")
        except ValueError as exc:
            assert "手动文本模式默认始发地" in str(exc)
        assert manager.snapshot() == modified_saved

        status = RuntimeStatus(os.path.join(directory, "output"))
        server = start_status_dashboard(status, "127.0.0.1", 0, configuration=manager)
        assert server is not None
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base, timeout=5) as response:
            html = response.read().decode("utf-8")
            csrf = response.headers.get("X-Freight-CSRF-Token", "")
        assert "群与线路管理" in html
        assert "manualDefaultOrigin" in html
        assert "function splitValues" in html
        assert "/[,，、;；\n" not in html
        code, api_config = request_json(base + "/api/config")
        assert code == 200 and len(api_config["groups"]) == 2
        assert api_config["groups"][1]["name"] == "新群已修改"
        expanded_for_api = copy.deepcopy(expanded)
        expanded_for_api["version"] = api_config["version"]
        code, api_result = request_json(
            base + "/api/config", "POST", expanded_for_api, csrf
        )
        assert code == 200 and api_result["ok"]
        code, current_api_config = request_json(base + "/api/config")
        assert code == 200
        invalid = {
            **expanded,
            "version": current_api_config["version"],
            "groups": [{"id": "abc", "name": "坏群", "default_origin": "贵港"}],
        }
        code, api_result = request_json(base + "/api/config", "POST", invalid, csrf)
        assert code == 400 and "纯数字" in api_result["error"]
        server.shutdown()
        server.server_close()

    print("ROUTE_MANAGEMENT_TESTS_OK")


if __name__ == "__main__":
    main()
