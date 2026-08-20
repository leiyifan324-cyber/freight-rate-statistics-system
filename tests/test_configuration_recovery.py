import json
import os
import subprocess
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)

from freight_runtime import ConfigurationConflictError, FreightConfigurationManager
from qq_freight_parser import load_qq_live_config


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def create_files(directory, token="secret-token"):
    live = os.path.join(directory, "qq_live_config.json")
    rules = os.path.join(directory, "freight_rules.json")
    write_json(live, {
        "ws_url": "ws://127.0.0.1:3001",
        "access_token": token,
        "group_ids": ["10001"],
        "group_names": {"10001": "测试"},
        "group_default_origins": {"10001": "南宁"},
        "output_root": os.path.join(directory, "data"),
        "rules_file": rules,
        "status_dashboard": {"enabled": False, "host": "127.0.0.1", "port": 8765},
    })
    write_json(rules, {
        "default_origin": "南宁",
        "origin_groups": {"南宁": ["南宁"]},
        "destination_groups": {"佛山": ["佛山"]},
        "board_types": ["大板"],
        "price_threshold": 1000,
    })
    return live, rules


class CrashAfterFirstReplace(FreightConfigurationManager):
    writes = 0

    @staticmethod
    def _atomic_write_json(path, value):
        FreightConfigurationManager._atomic_write_json(path, value)
        CrashAfterFirstReplace.writes += 1
        if CrashAfterFirstReplace.writes == 2:
            # First write is the transaction journal; second is the first config file.
            os._exit(77)


def run_crash_child(live, rules):
    manager = CrashAfterFirstReplace(live, rules)
    payload = manager.snapshot()
    payload["origins"] = [{"name": "梧州", "aliases": ["梧州"]}]
    payload["default_origin"] = "梧州"
    payload["groups"][0]["default_origin"] = "梧州"
    manager.apply(payload)


def assert_rejected(callable_value):
    try:
        callable_value()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--crash-child":
        run_crash_child(sys.argv[2], sys.argv[3])
        return

    with tempfile.TemporaryDirectory(prefix="freight-config-recovery-") as directory:
        live, rules = create_files(directory)
        manager = FreightConfigurationManager(live, rules)
        snapshot = manager.snapshot()
        assert snapshot["connection"]["access_token"] == ""
        assert snapshot["connection"]["access_token_configured"] is True
        assert snapshot["version"]

        stale = json.loads(json.dumps(snapshot, ensure_ascii=False))
        updated = json.loads(json.dumps(snapshot, ensure_ascii=False))
        updated["groups"][0]["name"] = "新群名"
        manager.apply(updated)
        try:
            manager.apply(stale)
        except ConfigurationConflictError:
            pass
        else:
            raise AssertionError("stale configuration was accepted")

        current = manager.snapshot()
        current["connection"]["access_token"] = ""
        manager.apply(current)
        with open(live, "r", encoding="utf-8") as stream:
            assert json.load(stream)["access_token"] == "secret-token"

        malformed = manager.snapshot()
        malformed["connection"]["ws_url"] = "ws://"
        assert_rejected(lambda: manager.apply(malformed))

        for index in range(55):
            rotating = manager.snapshot()
            rotating["groups"][0]["name"] = f"备份-{index}"
            manager.apply(rotating)
        backup_root = os.path.join(directory, "配置备份")
        backup_count = sum(
            1 for entry in os.scandir(backup_root)
            if entry.is_dir(follow_symlinks=False)
        )
        assert backup_count == 50, backup_count

    with tempfile.TemporaryDirectory(prefix="freight-config-hard-crash-") as directory:
        live, rules = create_files(directory, token="")
        process = subprocess.run(
            [sys.executable, __file__, "--crash-child", live, rules],
            timeout=10,
        )
        assert process.returncode == 77, process.returncode
        recovered = FreightConfigurationManager(live, rules)
        recovered_snapshot = recovered.snapshot()
        assert recovered_snapshot["groups"][0]["default_origin"] == "南宁"
        assert recovered_snapshot["default_origin"] == "南宁"
        load_qq_live_config(live)
        assert not os.path.exists(recovered.transaction_path)

    with tempfile.TemporaryDirectory(prefix="freight-invalid-dashboard-") as directory:
        live, _rules = create_files(directory, token="")
        with open(live, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        value["status_dashboard"]["port"] = 70000
        write_json(live, value)
        assert_rejected(lambda: load_qq_live_config(live))
        value["status_dashboard"]["port"] = 8765
        value["status_dashboard"]["host"] = "0.0.0.0"
        write_json(live, value)
        assert_rejected(lambda: load_qq_live_config(live))

    print(json.dumps({
        "token_redacted_and_preserved": True,
        "stale_version_rejected": True,
        "malformed_ws_rejected": True,
        "hard_crash_auto_recovered": True,
        "dashboard_bind_validated": True,
        "configuration_backups_bounded": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
