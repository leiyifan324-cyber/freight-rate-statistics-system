import csv
import importlib.util
import json
import os
import sys
import tempfile
import warnings
from datetime import date


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)
PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")


def load_freight_module():
    spec = importlib.util.spec_from_file_location(
        "freight_spring_large_archive_test",
        PARSER_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(record_date, price):
    return {
        "日期": record_date,
        "发布人": "测试员",
        "始发地": "南宁",
        "目的地": "佛山",
        "目的城市": "佛山",
        "货物小类": "大板",
        "货物大类": "板材",
        "报价文本": str(price),
        "最低报价": price,
        "最高报价": price,
        "平均报价": price,
        "是否整车价": "否",
        "原始消息": f"南宁到佛山大板{price}-{record_date}",
    }


def csv_dates(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as stream:
        return [row["日期"] for row in csv.DictReader(stream)]


def main():
    warnings.filterwarnings("ignore", message="Glyph .* missing from font")
    freight = load_freight_module()
    freight.CARGO_TYPES = {
        "大板": {"aliases": ["大板"], "category": "板材"},
    }
    records = [
        record("2024-02-10", 200),
        record("2025-01-28", 210),
        record("2025-01-29", 220),
        record("2026-02-16", 230),
        record("2026-02-17", 240),
        record("2027-02-06", 250),
    ]

    with tempfile.TemporaryDirectory(prefix="freight-spring-large-") as temp_root:
        previous_directory = os.getcwd()
        try:
            os.chdir(temp_root)
            database_path = os.path.join(temp_root, freight.DATABASE_FILE)
            database = freight.FreightDatabase(database_path)
            database.insert_records([
                (freight.build_record_content_dedup_key(item), item)
                for item in records
            ], source="spring_test")

            summary = freight.rebuild_all(
                as_of_date=date(2027, 2, 7),
                database_file=database_path,
            )
            assert summary["current_spring_year"] == 2027
            assert summary["active_records"] == 1
            assert csv_dates(os.path.join(temp_root, freight.DETAIL_CSV)) == [
                "2027-02-06"
            ]

            archive_2026 = os.path.join(
                temp_root,
                freight.BUSINESS_ARCHIVE_FOLDER,
                "2026春节年度",
                freight.LARGE_CATEGORY_ARCHIVE_FOLDER,
            )
            assert csv_dates(os.path.join(archive_2026, freight.DETAIL_CSV)) == [
                "2026-02-17"
            ]
            for filename in (
                freight.DAILY_CSV,
                freight.WEEKLY_CSV,
                freight.MONTHLY_CSV,
                freight.YEARLY_CSV,
                freight.REPORT_XLSX,
            ):
                assert os.path.isfile(os.path.join(archive_2026, filename)), filename
            assert os.path.isdir(os.path.join(archive_2026, freight.CHART_FOLDER))

            state_path = os.path.join(
                archive_2026,
                freight.LARGE_CATEGORY_ARCHIVE_STATE,
            )
            state_before = freight.read_json_object(state_path)
            mtime_before = os.stat(state_path).st_mtime_ns
            second = freight.rebuild_all(
                as_of_date=date(2027, 2, 7),
                database_file=database_path,
            )
            assert 2026 in second["large_category_archives"]["skipped_years"]
            assert os.stat(state_path).st_mtime_ns == mtime_before

            late = record("2026-03-02", 245)
            database.insert_records([
                (freight.build_record_content_dedup_key(late), late)
            ], source="late_spring_test")
            freight.rebuild_all(
                as_of_date=date(2027, 2, 7),
                database_file=database_path,
            )
            state_after = freight.read_json_object(state_path)
            assert state_after["fingerprint"] != state_before["fingerprint"]
            assert csv_dates(os.path.join(archive_2026, freight.DETAIL_CSV)) == [
                "2026-02-17",
                "2026-03-02",
            ]
        finally:
            os.chdir(previous_directory)

    print(json.dumps({
        "large_current_outputs_reset_at_spring_festival": True,
        "large_old_year_outputs_archived": True,
        "offline_multi_year_recovery": True,
        "large_archive_idempotent": True,
        "late_old_record_refreshes_archive": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
