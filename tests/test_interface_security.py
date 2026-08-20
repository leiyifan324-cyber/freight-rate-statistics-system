import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from freight_runtime import FreightConfigurationManager, RuntimeStatus, start_status_dashboard


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def request(base, path, method="GET", payload=None, headers=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    actual_headers = dict(headers or {})
    if payload is not None:
        actual_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=actual_headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class SlowManager(FreightConfigurationManager):
    def apply(self, payload):
        time.sleep(0.15)
        return super().apply(payload)


def main():
    with tempfile.TemporaryDirectory(prefix="freight-interface-security-") as directory:
        live = os.path.join(directory, "qq_live_config.json")
        rules = os.path.join(directory, "freight_rules.json")
        test_access_token = "do-not-" + "return"
        write_json(live, {
            "ws_url": "ws://127.0.0.1:3001",
            "access_token": test_access_token,
            "group_ids": ["10001"],
            "group_names": {"10001": "测试"},
            "group_default_origins": {"10001": "南宁"},
        })
        write_json(rules, {
            "default_origin": "南宁",
            "origin_groups": {"南宁": ["南宁"]},
            "destination_groups": {"佛山": ["佛山"]},
            "board_types": ["大板"],
            "price_threshold": 1000,
        })
        restarts = []
        manager = SlowManager(live, rules, restart_callback=lambda: restarts.append(True))
        server = start_status_dashboard(
            RuntimeStatus(os.path.join(directory, "status")),
            "127.0.0.1",
            0,
            configuration=manager,
            recent_data_provider=lambda _limit: {"items": []},
        )
        assert server is not None
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            code, headers, _ = request(base, "/")
            assert code == 200
            csrf = headers.get("X-Freight-CSRF-Token")
            assert csrf and len(csrf) >= 32
            assert headers.get("X-Content-Type-Options") == "nosniff"
            assert headers.get("Content-Security-Policy")

            code, _, body = request(base, "/api/config")
            config = json.loads(body.decode("utf-8"))
            assert code == 200
            assert config["connection"]["access_token"] == ""
            assert config["connection"]["access_token_configured"] is True

            code, _, _ = request(base, "/unknown")
            assert code == 404
            code, _, _ = request(base, "/api/recent?limit=0")
            assert code == 400
            code, _, _ = request(base, "/api/status", headers={"Host": "evil.invalid"})
            assert code == 400

            code, _, _ = request(base, "/api/config", method="POST", payload=config)
            assert code == 403
            code, _, _ = request(
                base,
                "/api/config",
                method="POST",
                payload=config,
                headers={"Origin": base},
            )
            assert code == 403

            common_headers = {"Origin": base, "X-Freight-CSRF": csrf}
            missing_version = json.loads(json.dumps(config, ensure_ascii=False))
            missing_version.pop("version")
            code, _, _ = request(
                base,
                "/api/config",
                method="POST",
                payload=missing_version,
                headers=common_headers,
            )
            assert code == 428
            unknown_field = json.loads(json.dumps(config, ensure_ascii=False))
            unknown_field["typo_field"] = True
            code, _, _ = request(
                base,
                "/api/config",
                method="POST",
                payload=unknown_field,
                headers=common_headers,
            )
            assert code == 400
            start = threading.Event()
            payloads = []
            for index in range(8):
                value = json.loads(json.dumps(config, ensure_ascii=False))
                value["groups"][0]["name"] = f"并发-{index}"
                payloads.append(value)

            def post(value):
                start.wait(2)
                code, _, _ = request(
                    base,
                    "/api/config",
                    method="POST",
                    payload=value,
                    headers=common_headers,
                )
                return code

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(post, value) for value in payloads]
                start.set()
                codes = [future.result(timeout=8) for future in futures]
            assert codes.count(200) == 1, codes
            assert all(code in {200, 409} for code in codes), codes
        finally:
            server.shutdown()
            server.server_close()

    print(json.dumps({
        "secret_redacted": True,
        "csrf_and_origin_required": True,
        "security_headers_present": True,
        "strict_http_contract": True,
        "host_and_schema_validated": True,
        "concurrent_config_single_flight": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
