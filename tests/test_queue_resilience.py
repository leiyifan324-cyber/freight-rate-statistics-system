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

from freight_runtime import RuntimeStatus


PARSER_PATH = os.path.join(SRC_DIR, "qq_freight_parser.py")
RULES_PATH = os.path.join(ROOT, "config", "freight_rules.default.json")


def load_freight():
    spec = importlib.util.spec_from_file_location("freight_queue_resilience", PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(message_id, price=235):
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": 10001,
        "message_id": message_id,
        "time": time.time(),
        "user_id": 20001,
        "sender": {"nickname": "韧性测试"},
        "message": [{"type": "text", "data": {"text": f"南宁到佛山大板{price}"}}],
    }


def main():
    freight = load_freight()
    freight.load_rules_config(RULES_PATH)
    with tempfile.TemporaryDirectory(
        prefix="freight-durable-queue-",
        ignore_cleanup_errors=True,
    ) as directory:
        config = {
            "group_ids": {"10001"},
            "group_names": {"10001": "测试"},
            "group_default_origins": {"10001": "南宁"},
            "output_root": directory,
            "rules_file": RULES_PATH,
        }
        profiles = freight.build_qq_group_profiles(config)
        inner = freight.OneBotFreightIngestor(profiles)
        entered = threading.Event()
        release = threading.Event()

        class BlockingIngestor:
            databases = inner.databases

            def ingest(self, value):
                entered.set()
                release.wait(10)
                return inner.ingest(value)

        status = RuntimeStatus(os.path.join(directory, "status"))
        processor = freight.GroupMessageProcessor(profiles, BlockingIngestor(), status)
        processor.submit(event("first", 200))
        assert entered.wait(2)
        for index in range(200):
            processor.submit(event(f"queued-{index}", 201 + index))
        database = inner.databases["10001"]
        assert database.count_pending_inbound_events() >= 200
        assert processor._queues["10001"].maxsize > 0
        assert processor._queues["10001"].qsize() <= processor._queues["10001"].maxsize
        assert processor.stop(drain=True, timeout=0.05) is False
        assert any(thread.is_alive() for thread in processor._threads)
        release.set()
        assert processor.wait_idle(60), status.snapshot()
        assert processor.stop(drain=True, timeout=2) is True
        assert database.count_pending_inbound_events() == 0
        assert database.count_records() == 201

        # A persisted event from a previous process must be consumed after restart.
        database.enqueue_inbound_event("recovered-event", event("recovered", 999))
        recovered = freight.GroupMessageProcessor(profiles, inner, status)
        assert recovered.wait_idle(10)
        assert recovered.stop(drain=True, timeout=2) is True
        assert database.count_pending_inbound_events() == 0
        assert database.count_records() == 202
        status.close()

    with tempfile.TemporaryDirectory(prefix="freight-excel-isolation-") as directory:
        profiles = {
            "1": {"group_id": "1", "group_name": "一群", "output_dir": os.path.join(directory, "1")},
            "2": {"group_id": "2", "group_name": "二群", "output_dir": os.path.join(directory, "2")},
        }
        for profile in profiles.values():
            os.makedirs(profile["output_dir"])
            open(os.path.join(profile["output_dir"], freight.REPORT_XLSX), "wb").close()
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()

        def incremental(profile, _records):
            if profile["group_id"] == "1":
                first_started.set()
                release_first.wait(10)
            else:
                second_started.set()
            return {}

        excel_status = RuntimeStatus(os.path.join(directory, "status"))
        coordinator = freight.ExcelRebuildCoordinator(
            profiles,
            excel_status,
            rebuild_callback=lambda _profile: {},
            incremental_callback=incremental,
            batch_delay_seconds=0,
            full_refresh_seconds=60,
        )
        coordinator.request("1", records=[{"id": 1}])
        assert first_started.wait(2)
        coordinator.request("2", records=[{"id": 2}])
        assert second_started.wait(2), "group 2 was blocked by group 1"
        assert coordinator.stop(drain=True, timeout=0.05) is False
        release_first.set()
        assert coordinator.wait_idle(5)
        assert coordinator.stop(drain=True, timeout=2) is True
        excel_status.close()

    print(json.dumps({
        "durable_queue_replayed": True,
        "memory_wakeup_queue_bounded": True,
        "drain_timeout_reported": True,
        "excel_groups_isolated": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
