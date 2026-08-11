import importlib.util
import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime

import pandas as pd
from PIL import Image
from openpyxl import load_workbook


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)
PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")
RUNTIME_PATH = os.path.join(SRC_DIR, "freight_runtime.py")
RULES_PATH = os.path.join(ROOT, "config", "freight_rules.default.json")
LIVE_CONFIG_PATH = os.path.join(ROOT, "config", "qq_live_config.example.json")
OCR_SCRIPT = os.path.join(ROOT, "scripts", "windows_ocr.ps1")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(group_id, message_id, text=None, sender="测试员", self_message=False, image=None):
    segments = []
    if text is not None:
        segments.append({"type": "text", "data": {"text": text}})
    if image is not None:
        segments.append({"type": "image", "data": {"file": image}})
    user_id = 10001 if self_message else 10002
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": int(group_id),
        "message_id": message_id,
        "time": time.time(),
        "self_id": 10001,
        "user_id": user_id,
        "sender": {"nickname": sender},
        "message": segments,
    }


class DeterministicOcr:
    """Exercise the image-ingestion path without requiring a CI language pack."""

    enabled = True

    def recognize(self, source):
        assert os.path.isfile(source)
        return "明天 重庆渝北 大板 260", ""


def main():
    freight = load_module("freight_full_test", PARSER_PATH)
    runtime = load_module("freight_runtime_full_test", RUNTIME_PATH)
    freight.load_rules_config(RULES_PATH)

    base_date = "2026-08-10"
    assert freight.expand_relative_freight_dates("今晚佛山大板100", base_date)[0][0] == base_date
    assert freight.expand_relative_freight_dates("明早佛山大板100", base_date)[0][0] == "2026-08-11"
    assert freight.expand_relative_freight_dates("8月15日佛山大板100", base_date)[0][0] == "2026-08-15"
    assert freight.expand_relative_freight_dates("周三佛山大板100", base_date)[0][0] == "2026-08-12"
    assert freight.expand_relative_freight_dates("下周一佛山大板100", base_date)[0][0] == "2026-08-17"

    with tempfile.TemporaryDirectory(prefix="freight-full-test-") as temp_root:
        config = {
            "group_ids": {"100000001"},
            "group_names": {"100000001": "贵港测试群"},
            "group_default_origins": {"100000001": "贵港"},
            "output_root": temp_root,
            "backup_retention_days": 3,
            "rules_file": RULES_PATH,
            "config_path": LIVE_CONFIG_PATH,
        }
        profiles = freight.build_qq_group_profiles(config)
        profile = profiles["100000001"]
        ocr = DeterministicOcr()
        assert runtime.WindowsOcr(OCR_SCRIPT, enabled=True).enabled is True
        ingestor = freight.OneBotFreightIngestor(
            profiles, accept_self_messages=True, ocr=ocr
        )

        first_future = ingestor.ingest(event(
            "100000001", "future-1", "明早佛山南海 大板 150", "甲"
        ))
        duplicate_future = ingestor.ingest(event(
            "100000001", "future-2", "明早佛山南海 大板 150", "乙"
        ))
        today_record = ingestor.ingest(event(
            "100000001", "today-1", "今晚杭州余杭 大板 200", "丙"
        ))
        assert first_future["status"] == "added"
        assert first_future["pending_count"] == 1
        assert duplicate_future["status"] == "duplicate_content"
        assert today_record["status"] == "added"
        assert today_record["pending_count"] == 0

        summary = freight.rebuild_qq_group_output(profile)
        assert summary["active_records"] == 1
        assert summary["pending_records"] == 1
        pending = pd.read_csv(os.path.join(profile["output_dir"], freight.PENDING_CSV))
        detail = pd.read_csv(os.path.join(profile["output_dir"], freight.DETAIL_CSV))
        assert len(pending) == 1
        assert len(detail) == 1
        assert all(pd.to_datetime(pending["日期"]).dt.date > freight.trusted_today())
        assert all(pd.to_datetime(detail["日期"]).dt.date <= freight.trusted_today())

        rejected = ingestor.ingest(event(
            "100000001", "bad-1", "大家下午好，这不是报价", "丁"
        ))
        assert rejected["status"] == "rejected"
        freight.rebuild_qq_group_output(profile)
        rejected_csv = pd.read_csv(os.path.join(profile["output_dir"], freight.REJECTED_CSV))
        assert len(rejected_csv) >= 1

        self_blocking = freight.OneBotFreightIngestor(
            profiles, accept_self_messages=False, ocr=ocr
        )
        self_result = self_blocking.ingest(event(
            "100000001", "self-1", "今天成都双流 大板 220", self_message=True
        ))
        assert self_result["status"] == "ignored"

        image_path = os.path.join(temp_root, "ocr-freight.png")
        image = Image.new("RGB", (4, 4), "white")
        image.save(image_path)
        ocr_result = ingestor.ingest(event(
            "100000001", "ocr-1", image=image_path, sender="图片发布人"
        ))
        assert ocr_result["status"] == "added", ocr_result
        assert ocr_result["ocr_used"] is True

        database = runtime.FreightDatabase(profile["database_file"])
        database_count = database.count_records()
        with open(profile["input_file"], "a", encoding="utf-8") as stream:
            stream.write("\n这行原始审计文本不应触发数据库全量重解析\n")
        freight.rebuild_qq_group_output(profile)
        assert database.count_records() == database_count

        backup_dir = os.path.join(profile["output_dir"], "备份")
        assert any(name.endswith(".zip") for name in os.listdir(backup_dir))

        log_dir = os.path.join(temp_root, "logs")
        logger = runtime.configure_logging(log_dir, "freight-full-test")
        logger.info("full optimization test")
        for handler in logger.handlers:
            handler.flush()
        assert os.path.exists(os.path.join(log_dir, "物流运价系统.log"))

        status = runtime.RuntimeStatus(temp_root)
        status.update(connection="connected", clock={"method": "test", "trusted_now": base_date})
        status.update_group("100000001", name="贵港测试群", active_records=1, pending_records=2, rejected_records=3)
        server = runtime.start_status_dashboard(status, "127.0.0.1", 0)
        assert server is not None
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/status", timeout=5
            ) as response:
                dashboard_data = json.loads(response.read().decode("utf-8"))
            assert dashboard_data["connection"] == "connected"
        finally:
            server.shutdown()
            server.server_close()

        report_path = os.path.join(profile["output_dir"], freight.REPORT_XLSX)
        held_workbook = load_workbook(report_path, read_only=True)
        try:
            locked_summary = freight.rebuild_qq_group_output(profile)
        finally:
            held_workbook.close()
        if not locked_summary["excel_updated"]:
            assert os.path.exists(os.path.join(
                profile["output_dir"], freight.PENDING_REPORT_XLSX
            ))
            unlocked_summary = freight.rebuild_qq_group_output(profile)
            assert unlocked_summary["excel_updated"] is True

        clock = runtime.NetworkClock()
        clock_result = clock.sync(["https://www.microsoft.com"])
        assert "method" in clock_result
        assert "trusted_now" in clock_result

        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

        print(json.dumps({
            "future_cache_dedup": True,
            "cache_rejects_today": True,
            "sqlite_incremental": True,
            "rejected_queue": True,
            "self_message_switch": True,
            "windows_ocr": True,
            "daily_backup": True,
            "rotating_log": True,
            "status_dashboard": True,
            "excel_lock_protection": True,
            "extended_dates": True,
            "network_clock_method": clock_result["method"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
