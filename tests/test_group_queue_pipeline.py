import importlib.util
import json
import os
import sys
import tempfile
import threading
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
sys.path.insert(0, SRC_DIR)

import freight_runtime  # noqa: E402


PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")
RULES_PATH = os.path.join(ROOT, "config", "freight_rules.default.json")


def load_freight_module():
    spec = importlib.util.spec_from_file_location("freight_queue_test", PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_event(group_id, message_id, price):
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": int(group_id),
        "message_id": message_id,
        "time": time.time(),
        "self_id": 1765614718,
        "user_id": 2000000001,
        "sender": {"nickname": f"发送者{group_id}"},
        "message": [{
            "type": "text",
            "data": {"text": f"南宁到佛山大板{price}"},
        }],
    }


def main():
    freight = load_freight_module()
    freight.load_rules_config(RULES_PATH)

    with tempfile.TemporaryDirectory(prefix="freight-queue-") as temp_root:
        config = {
            "group_ids": {"1001", "1002"},
            "group_names": {"1001": "慢群", "1002": "快群"},
            "group_default_origins": {"1001": "南宁", "1002": "南宁"},
            "output_root": temp_root,
            "rules_file": RULES_PATH,
        }
        profiles = freight.build_qq_group_profiles(config)
        inner_ingestor = freight.OneBotFreightIngestor(profiles)

        class DelayedIngestor:
            def ingest(self, event):
                if str(event.get("group_id")) == "1001":
                    time.sleep(0.12)
                return inner_ingestor.ingest(event)

        rebuild_lock = threading.Lock()
        rebuild_active = 0
        rebuild_max_active = 0
        rebuild_calls = []

        def fake_rebuild(profile):
            nonlocal rebuild_active, rebuild_max_active
            with rebuild_lock:
                rebuild_active += 1
                rebuild_max_active = max(rebuild_max_active, rebuild_active)
            try:
                time.sleep(0.02)
                rebuild_calls.append(profile["group_id"])
                return {
                    "active_records": inner_ingestor.databases[
                        profile["group_id"]
                    ].count_records(),
                    "pending_records": 0,
                    "rejected_records": 0,
                    "excel_updated": True,
                    "as_of_date": freight.trusted_today().isoformat(),
                }
            finally:
                with rebuild_lock:
                    rebuild_active -= 1

        status = freight_runtime.RuntimeStatus(os.path.join(temp_root, "status"))
        coordinator = freight.ExcelRebuildCoordinator(
            profiles,
            status,
            rebuild_callback=fake_rebuild,
        )
        processor = freight.GroupMessageProcessor(
            profiles,
            DelayedIngestor(),
            status,
            result_callback=lambda _event, result: coordinator.request(
                result.get("group_id", "")
            ),
        )
        try:
            started = time.perf_counter()
            for index in range(10):
                processor.submit(make_event("1001", f"slow-{index}", 200 + index))
                processor.submit(make_event("1002", f"fast-{index}", 300 + index))
            enqueue_ms = (time.perf_counter() - started) * 1000

            deadline = time.time() + 1.5
            fast_processed_first = False
            while time.time() < deadline:
                groups = status.snapshot().get("groups", {})
                fast_count = groups.get("1002", {}).get("processed_events", 0)
                slow_count = groups.get("1001", {}).get("processed_events", 0)
                if fast_count == 10 and slow_count < 10:
                    fast_processed_first = True
                    break
                time.sleep(0.01)

            assert enqueue_ms < 500, enqueue_ms
            assert fast_processed_first, status.snapshot()
            assert processor.wait_idle(10), status.snapshot()
            assert coordinator.wait_idle(10), status.snapshot()
            assert inner_ingestor.databases["1001"].count_records() == 10
            assert inner_ingestor.databases["1002"].count_records() == 10
            assert rebuild_max_active == 1, rebuild_max_active
            assert set(rebuild_calls) == {"1001", "1002"}, rebuild_calls

            groups = status.snapshot()["groups"]
            for group_id in ("1001", "1002"):
                assert groups[group_id]["queue_depth"] == 0
                assert groups[group_id]["queue_state"] == "idle"
                assert groups[group_id]["received_events"] == 10
                assert groups[group_id]["processed_events"] == 10
                assert groups[group_id]["excel_state"] == "ready"
        finally:
            processor.stop(drain=True)
            coordinator.stop(drain=True)

    print(json.dumps({
        "non_blocking_enqueue": True,
        "per_group_isolation": True,
        "per_group_ordered_processing": True,
        "excel_rebuild_serialized": True,
        "excel_requests_coalesced": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
