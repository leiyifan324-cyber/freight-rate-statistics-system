import importlib.util
import json
import os
import sys
import tempfile
import time
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)

import freight_runtime  # noqa: E402


PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")
RULES_PATH = os.path.join(ROOT, "config", "freight_rules.default.json")


def load_freight_module():
    spec = importlib.util.spec_from_file_location("freight_event_test", PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_event(post_type, message_id, text, self_message=False, message_type=None):
    event = {
        "post_type": post_type,
        "group_id": 123456789,
        "message_id": message_id,
        "time": time.time(),
        "self_id": 1765614718,
        "user_id": 1765614718 if self_message else 2000000001,
        "sender": {"nickname": "测试发送者"},
        "message": [{"type": "text", "data": {"text": text}}],
    }
    if message_type is not None:
        event["message_type"] = message_type
    return event


def main():
    freight = load_freight_module()
    freight.load_rules_config(RULES_PATH)

    with tempfile.TemporaryDirectory(prefix="freight-event-test-") as temp_root:
        config = {
            "group_ids": {"123456789"},
            "group_names": {"123456789": "测试"},
            "group_default_origins": {"123456789": "南宁"},
            "output_root": temp_root,
            "rules_file": RULES_PATH,
        }
        profiles = freight.build_qq_group_profiles(config)

        self_ingestor = freight.OneBotFreightIngestor(
            profiles, accept_self_messages=True
        )
        self_result = self_ingestor.ingest(make_event(
            "message_sent", "self-1", "南宁到佛山大板235", self_message=True
        ))
        assert self_result["status"] == "added", self_result
        assert self_result["event_type"] == "message_sent"

        external_result = self_ingestor.ingest(make_event(
            "message", "external-1", "南宁到佛山红板236"
        ))
        assert external_result["status"] == "added", external_result

        disabled_ingestor = freight.OneBotFreightIngestor(
            profiles, accept_self_messages=False
        )
        disabled_result = disabled_ingestor.ingest(make_event(
            "message_sent", "self-disabled-1", "南宁到佛山大板237", self_message=True
        ))
        assert disabled_result["status"] == "ignored", disabled_result
        assert disabled_result["reason"] == "登录账号自己的消息已关闭"

        private_result = self_ingestor.ingest(make_event(
            "message", "private-1", "南宁到佛山大板238", message_type="private"
        ))
        assert private_result["status"] == "ignored", private_result
        assert private_result["reason"] == "不是群消息"

        status = freight_runtime.RuntimeStatus(os.path.join(temp_root, "status"))
        freight.update_runtime_event_status(
            status, profiles, make_event(
                "message_sent", "status-1", "南宁到佛山大板239", self_message=True
            ), self_result,
        )
        snapshot = status.snapshot()
        group_status = snapshot["groups"]["123456789"]
        assert snapshot["last_event_type"] == "message_sent"
        assert snapshot["last_event_status"] == "added"
        assert snapshot["last_event_reason"] == "新增 1 条运价"
        assert group_status["last_event_type"] == "message_sent"
        assert group_status["last_event_status"] == "added"
        assert group_status["last_event_reason"] == "新增 1 条运价"
        assert group_status["last_message_id"] == "status-1"

        server = freight_runtime.start_status_dashboard(status, "127.0.0.1", 0)
        assert server is not None
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/api/status", timeout=5
            ) as response:
                api_status = json.loads(response.read().decode("utf-8"))
            assert api_status["last_event_type"] == "message_sent"
            assert api_status["groups"]["123456789"]["last_event_status"] == "added"
        finally:
            server.shutdown()
            server.server_close()

    print(json.dumps({
        "message_event": "accepted",
        "message_sent_event": "accepted_when_enabled",
        "self_message_switch": "preserved",
        "private_event": "ignored",
        "status_feedback": "immediate",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
