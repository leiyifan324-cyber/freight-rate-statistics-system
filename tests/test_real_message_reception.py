import importlib.util
import json
import os
import sys
import tempfile
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)

import freight_runtime  # noqa: E402


PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")
RULES_PATH = os.path.join(ROOT, "config", "freight_rules.default.json")


def load_freight_module():
    spec = importlib.util.spec_from_file_location("freight_real_message_test", PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_event(message_id, text):
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123456789,
        "message_id": message_id,
        "time": time.time(),
        "self_id": 1765614718,
        "user_id": 2000000001,
        "sender": {"nickname": "真实消息测试"},
        "message": [{"type": "text", "data": {"text": text}}],
    }


def main():
    with open(RULES_PATH, "rb") as stream:
        rules_before = stream.read()

    freight = load_freight_module()
    freight.load_rules_config(RULES_PATH)
    assert freight.DEFAULT_ORIGIN == "贵港"
    assert freight.PRICE_THRESHOLD == 1000

    formatted, reason = freight.format_freight_line_with_reason(
        "南宁到佛山南海235", "南宁"
    )
    assert formatted == "南宁到佛山南海 大板 235", (formatted, reason)
    assert reason == ""

    rejection_examples = {
        "八塘到宁波余姚大板235": "始发地不在范围内",
        "南宁到黄冈武穴大板160": "目的地不在范围内",
        "南宁到佛山大板": "未识别到有效价格",
        "南宁到佛山求车235": "命中无效关键词: 求车",
    }
    for message, expected_reason in rejection_examples.items():
        formatted, reason = freight.format_freight_line_with_reason(message, "南宁")
        assert formatted is None, (message, formatted)
        assert expected_reason in reason, (message, reason)

    with tempfile.TemporaryDirectory(prefix="freight-real-message-") as temp_root:
        config = {
            "group_ids": {"123456789"},
            "group_names": {"123456789": "测试"},
            "group_default_origins": {"123456789": "南宁"},
            "output_root": temp_root,
            "rules_file": RULES_PATH,
        }
        profiles = freight.build_qq_group_profiles(config)
        ingestor = freight.OneBotFreightIngestor(profiles)
        event = make_event(
            "real-mixed-1",
            "【报价】南宁→佛山南海 大板 235元/方\n"
            "南宁到黄冈武穴大板160\n"
            "南宁到佛山大板",
        )
        result = ingestor.ingest(event)
        assert result["status"] == "added", result
        assert result["inserted_count"] == 1, result
        assert result["received_line_count"] == 3, result
        assert result["rejected_line_count"] == 2, result
        assert "另有 2 行未识别" in result["reason"], result
        assert any("目的地不在范围内" in value for value in result["rejected_reasons"])
        assert "未识别到有效价格" in result["rejected_reasons"]

        rejections = ingestor.databases["123456789"].fetch_rejections()
        reasons = [row["原因"] for row in rejections]
        assert any("目的地不在范围内" in value for value in reasons), reasons
        assert "未识别到有效价格" in reasons, reasons

        future_result = ingestor.ingest(make_event(
            "real-future-1",
            "明天南宁到杭州红板240",
        ))
        assert future_result["status"] == "added", future_result
        assert future_result["pending_count"] == 1, future_result

        recent = freight.build_recent_data_snapshot(profiles, 20)
        kinds = {item["kind"] for item in recent["items"]}
        assert {"正式识别", "待生效", "不合格"}.issubset(kinds), recent

        status = freight_runtime.RuntimeStatus(os.path.join(temp_root, "status"))
        freight.update_runtime_event_status(status, profiles, event, result)
        snapshot = status.snapshot()
        assert snapshot["last_event_reason"] == result["reason"]
        assert snapshot["groups"]["123456789"]["last_event_reason"] == result["reason"]
        assert snapshot["groups"]["123456789"]["active_records"] == 1
        assert snapshot["groups"]["123456789"]["rejected_records"] == 2

    with open(RULES_PATH, "rb") as stream:
        assert stream.read() == rules_before

    print(json.dumps({
        "mixed_message_partial_ingest": "passed",
        "per_line_rejection_reason": "passed",
        "recent_three_way_classification": "passed",
        "default_cargo_rule": "unchanged",
        "price_threshold": "unchanged",
        "rules_file": "unchanged",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
