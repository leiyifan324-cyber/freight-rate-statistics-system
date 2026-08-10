import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import date

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)
MODULE_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")


def load_module():
    spec = importlib.util.spec_from_file_location("freight", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_csv(path):
    return pd.read_csv(path)


def main():
    freight = load_module()

    assert freight.expand_relative_freight_dates(
        "明天佛山南海 大板 120", "2026-12-31"
    )[0][0] == "2027-01-01"
    assert freight.expand_relative_freight_dates(
        "后天佛山南海 大板 120", "2026-12-31"
    )[0][0] == "2027-01-02"
    assert [item[0] for item in freight.expand_relative_freight_dates(
        "明后天佛山南海 大板 120", "2026-12-31"
    )] == ["2027-01-01", "2027-01-02"]
    assert [item[0] for item in freight.expand_relative_freight_dates(
        "今明两天佛山南海 大板 120", "2026-12-31"
    )] == ["2026-12-31", "2027-01-01"]
    assert freight.expand_relative_freight_dates(
        "明天佛山南海 大板 120", "2028-02-28"
    )[0][0] == "2028-02-29"

    with tempfile.TemporaryDirectory(prefix="freight-future-test-") as temp_root:
        input_path = os.path.join(temp_root, freight.INPUT_FILE)
        with open(input_path, "w", encoding="utf-8") as stream:
            stream.write("测试用户: 2026-08-10 10:00:00\n")
            stream.write("今天佛山南海 大板 100\n")
            stream.write("明天杭州余杭 大板 200\n")
            stream.write("后天成都双流 大板 300\n")
            stream.write("明后天重庆渝北 大板 400\n")

        previous_directory = os.getcwd()
        try:
            os.chdir(temp_root)

            freight.rebuild_all("贵港", date(2026, 8, 10))
            active_day_1 = read_csv(freight.DETAIL_CSV)
            pending_day_1 = read_csv(freight.PENDING_CSV)
            assert len(active_day_1) == 1
            assert len(pending_day_1) == 4
            assert set(active_day_1["日期"]) == {"2026-08-10"}
            assert set(pending_day_1["日期"]) == {"2026-08-11", "2026-08-12"}
            with pd.ExcelFile(freight.REPORT_XLSX) as workbook:
                assert "待生效运价" in workbook.sheet_names

            freight.rebuild_all("贵港", date(2026, 8, 11))
            active_day_2 = read_csv(freight.DETAIL_CSV)
            pending_day_2 = read_csv(freight.PENDING_CSV)
            assert len(active_day_2) == 3
            assert len(pending_day_2) == 2
            assert set(pending_day_2["日期"]) == {"2026-08-12"}

            freight.rebuild_all("贵港", date(2026, 8, 12))
            active_day_3 = read_csv(freight.DETAIL_CSV)
            pending_day_3 = read_csv(freight.PENDING_CSV)
            assert len(active_day_3) == 5
            assert pending_day_3.empty
        finally:
            os.chdir(previous_directory)

        group_root = os.path.join(temp_root, "group-cache")
        config = {
            "group_ids": {"100000001", "100000002"},
            "group_names": {
                "100000001": "贵港测试群",
                "100000002": "南宁测试群",
            },
            "group_default_origins": {
                "100000001": "贵港",
                "100000002": "南宁",
            },
            "output_root": group_root,
        }
        profiles = freight.build_qq_group_profiles(config)
        ingestor = freight.OneBotFreightIngestor(profiles)
        for group_id in profiles:
            result = ingestor.ingest({
                "post_type": "message",
                "message_type": "group",
                "group_id": int(group_id),
                "message_id": f"future-{group_id}",
                "time": time.time(),
                "sender": {"nickname": "缓存测试"},
                "message": [{
                    "type": "text",
                    "data": {"text": "明天佛山南海 大板 150"},
                }],
            })
            assert result["status"] == "added"
            freight.rebuild_qq_group_output(profiles[group_id])
            group_detail = read_csv(os.path.join(
                profiles[group_id]["output_dir"], freight.DETAIL_CSV
            ))
            group_pending = read_csv(os.path.join(
                profiles[group_id]["output_dir"], freight.PENDING_CSV
            ))
            assert group_detail.empty
            assert len(group_pending) == 1

        print(json.dumps({
            "day_1_active": len(active_day_1),
            "day_1_cached": len(pending_day_1),
            "day_2_active": len(active_day_2),
            "day_2_cached": len(pending_day_2),
            "day_3_active": len(active_day_3),
            "day_3_cached": len(pending_day_3),
            "cache_cleared_after_effective": pending_day_3.empty,
            "cross_year": True,
            "leap_year": True,
            "independent_group_caches": True,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
