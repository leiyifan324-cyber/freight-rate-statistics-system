import csv
import importlib.util
import json
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)
PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")


def load_freight_module():
    spec = importlib.util.spec_from_file_location(
        "freight_locked_csv_test",
        PARSER_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_rows(path):
    with open(path, "r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def main():
    freight = load_freight_module()
    with tempfile.TemporaryDirectory(prefix="freight-locked-csv-") as temp_root:
        target = os.path.join(temp_root, freight.DAILY_CSV)
        pending = os.path.join(temp_root, "每日平均运价_待更新.csv")
        first = [{
            "日期": "2026-08-19",
            "始发地": "南宁",
            "目的城市": "佛山",
            "货物": "板材",
            "平均运价": 200,
            "最低运价": 200,
            "最高运价": 200,
            "报价数量": 1,
        }]
        second = [{**first[0], "平均运价": 220, "最低运价": 220, "最高运价": 220}]
        assert freight.save_csv(first, target)

        real_replace = freight.os.replace

        def simulate_locked_target(source, destination):
            if os.path.abspath(destination) == os.path.abspath(target):
                raise PermissionError("simulated Windows CSV lock")
            return real_replace(source, destination)

        freight.os.replace = simulate_locked_target
        try:
            assert freight.save_csv(second, target) is False
        finally:
            freight.os.replace = real_replace

        assert float(read_rows(target)[0]["平均运价"]) == 200
        assert float(read_rows(pending)[0]["平均运价"]) == 220

        assert freight.save_csv(second, target)
        assert float(read_rows(target)[0]["平均运价"]) == 220
        assert not os.path.exists(pending)

    print(json.dumps({
        "locked_csv_does_not_abort_refresh": True,
        "pending_csv_contains_latest_data": True,
        "next_refresh_replaces_original": True,
        "pending_copy_removed_after_recovery": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
