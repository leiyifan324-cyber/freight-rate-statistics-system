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
    spec = importlib.util.spec_from_file_location("freight_burst_test", PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_event(group_id, index):
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": int(group_id),
        "message_id": f"burst-{group_id}-{index}",
        "time": time.time(),
        "self_id": 1765614718,
        "user_id": 2000000000 + int(group_id),
        "sender": {"nickname": f"压力用户{group_id}"},
        "message": [{
            "type": "text",
            "data": {"text": f"南宁到佛山大板{200 + index}"},
        }],
    }


def main():
    freight = load_freight_module()
    freight.load_rules_config(RULES_PATH)
    group_ids = ("1001", "1002", "1003")
    events_per_group = 100

    with tempfile.TemporaryDirectory(prefix="freight-burst-") as temp_root:
        config = {
            "group_ids": set(group_ids),
            "group_names": {group_id: f"压力群{group_id}" for group_id in group_ids},
            "group_default_origins": {group_id: "南宁" for group_id in group_ids},
            "output_root": temp_root,
            "rules_file": RULES_PATH,
        }
        profiles = freight.build_qq_group_profiles(config)
        ingestor = freight.OneBotFreightIngestor(profiles)
        status = freight_runtime.RuntimeStatus(os.path.join(temp_root, "status"))
        processor = freight.GroupMessageProcessor(profiles, ingestor, status)
        try:
            started = time.perf_counter()
            for index in range(events_per_group):
                for group_id in group_ids:
                    queued = processor.submit(make_event(group_id, index))
                    assert queued["status"] == "queued", queued
            enqueue_ms = (time.perf_counter() - started) * 1000
            assert enqueue_ms < 3000, enqueue_ms
            assert processor.wait_idle(30), status.snapshot()
            processing_seconds = time.perf_counter() - started

            snapshot = status.snapshot()
            for group_id in group_ids:
                assert ingestor.databases[group_id].count_records() == events_per_group
                group = snapshot["groups"][group_id]
                assert group["received_events"] == events_per_group
                assert group["processed_events"] == events_per_group
                assert group["queue_depth"] == 0
                assert group["queue_state"] == "idle"
        finally:
            processor.stop(drain=True)

    print(json.dumps({
        "groups": len(group_ids),
        "events": len(group_ids) * events_per_group,
        "enqueue_ms": round(enqueue_ms, 1),
        "processing_seconds": round(processing_seconds, 2),
        "lost_records": 0,
        "queues_drained": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
