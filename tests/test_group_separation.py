import importlib.util
import json
import os
import sys
import tempfile

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)
MODULE_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")


def load_freight_module():
    spec = importlib.util.spec_from_file_location("freight", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_event(group_id, message_id, nickname):
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": int(group_id),
        "message_id": message_id,
        "time": 1786348800,
        "sender": {"nickname": nickname},
        "message": [
            {"type": "text", "data": {"text": "佛山南海 大板 120"}}
        ],
    }


def main():
    freight = load_freight_module()
    with tempfile.TemporaryDirectory(prefix="freight-group-test-") as temp_root:
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
            "output_root": temp_root,
        }
        profiles = freight.build_qq_group_profiles(config)
        ingestor = freight.OneBotFreightIngestor(profiles)

        first = ingestor.ingest(make_event("100000001", "g1-1", "甲"))
        repost = ingestor.ingest(make_event("100000001", "g1-2", "乙"))
        same_event = ingestor.ingest(make_event("100000001", "g1-1", "甲"))
        second_group = ingestor.ingest(make_event("100000002", "g2-1", "丙"))
        ignored = ingestor.ingest(make_event("123456789", "x-1", "丁"))

        assert first["status"] == "added"
        assert repost["status"] == "duplicate_content"
        assert same_event["status"] == "duplicate"
        assert second_group["status"] == "added"
        assert ignored["status"] == "ignored"

        for profile in profiles.values():
            freight.rebuild_qq_group_output(profile)

        gg_dir = profiles["100000001"]["output_dir"]
        nn_dir = profiles["100000002"]["output_dir"]
        gg_detail = pd.read_csv(os.path.join(gg_dir, freight.DETAIL_CSV))
        nn_detail = pd.read_csv(os.path.join(nn_dir, freight.DETAIL_CSV))

        assert len(gg_detail) == 1
        assert len(nn_detail) == 1
        assert set(gg_detail["始发地"]) == {"贵港"}
        assert set(nn_detail["始发地"]) == {"南宁"}

        for output_dir in (gg_dir, nn_dir):
            assert os.path.exists(os.path.join(output_dir, freight.REPORT_XLSX))
            assert os.path.exists(os.path.join(output_dir, freight.DATABASE_FILE))
            assert not os.path.exists(os.path.join(output_dir, freight.HISTORY_DAILY_CSV))
            with pd.ExcelFile(os.path.join(output_dir, freight.REPORT_XLSX)) as workbook:
                assert "历史每日平均运价" in workbook.sheet_names

        manual_dir = os.path.join(temp_root, "manual-mode")
        os.makedirs(manual_dir, exist_ok=True)
        with open(os.path.join(manual_dir, freight.INPUT_FILE), "w", encoding="utf-8") as stream:
            stream.write("测试用户: 08-10 10:00:00\n")
            stream.write("佛山南海 大板 130\n")
        previous_directory = os.getcwd()
        try:
            os.chdir(manual_dir)
            freight.rebuild_all()
        finally:
            os.chdir(previous_directory)
        manual_detail = pd.read_csv(os.path.join(manual_dir, freight.DETAIL_CSV))
        assert len(manual_detail) == 1
        assert set(manual_detail["始发地"]) == {"贵港"}
        assert not os.path.exists(os.path.join(manual_dir, "QQ实时统计结果"))

        print(json.dumps({
            "group_1_origin": gg_detail.iloc[0]["始发地"],
            "group_1_rows_after_repost_dedup": len(gg_detail),
            "group_2_origin": nn_detail.iloc[0]["始发地"],
            "group_2_rows": len(nn_detail),
            "same_message_event_status": same_event["status"],
            "unselected_group_status": ignored["status"],
            "separate_excel": True,
            "separate_database_state": True,
            "manual_mode_preserved": True,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
