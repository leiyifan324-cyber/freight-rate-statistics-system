import copy
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timezone


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from freight_runtime import (  # noqa: E402
    DASHBOARD_HTML,
    FreightConfigurationManager,
    FreightDatabase,
    RuntimeStatus,
    prune_daily_backups,
)
from qq_freight_parser import (  # noqa: E402
    DataLifecycleManager,
    HISTORY_DAILY_CSV,
    build_qq_group_profiles,
    load_qq_live_config,
    merge_daily_history,
)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def insert_lifecycle_fixtures(database, now):
    old_time = "2026-06-01T00:00:00+00:00"
    recent_time = "2026-08-10T00:00:00+00:00"
    with database.session() as connection:
        for key, effective_date, created_at in (
            ("old", "2026-06-01", old_time),
            ("recent", "2026-08-10", recent_time),
            ("future", "2026-08-20", now.isoformat(timespec="seconds")),
        ):
            connection.execute(
                """
                INSERT INTO freight_records(
                    dedup_key, effective_date, record_json,
                    first_message_key, source, created_at
                ) VALUES (?, ?, ?, ?, 'test', ?)
                """,
                (key, effective_date, json.dumps({"日期": effective_date}), key, created_at),
            )
        for key, processed_at in (("old", old_time), ("recent", recent_time)):
            connection.execute(
                "INSERT INTO processed_messages(message_key, processed_at) VALUES (?, ?)",
                (key, processed_at),
            )
        for key, created_at in (("old", old_time), ("recent", recent_time)):
            connection.execute(
                """
                INSERT INTO rejected_messages(
                    rejection_key, message_key, group_id, sender, message_time,
                    reason, message_text, media_source, created_at
                ) VALUES (?, ?, '1', 'tester', ?, 'bad', 'text', '', ?)
                """,
                (key, key, created_at, created_at),
            )
        for key, status, received_at in (
            ("dead-old", "dead_letter", old_time),
            ("dead-recent", "dead_letter", recent_time),
            ("pending-old", "pending", old_time),
        ):
            connection.execute(
                """
                INSERT INTO inbound_events(
                    event_key, event_json, status, attempts,
                    next_attempt_at, last_error, received_at
                ) VALUES (?, '{}', ?, 5, 0, '', ?)
                """,
                (key, status, received_at),
            )


def main():
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(
        prefix="freight-lifecycle-", ignore_cleanup_errors=True
    ) as directory:
        database_path = os.path.join(directory, "group", "运价数据.db")
        database = FreightDatabase(database_path)
        insert_lifecycle_fixtures(database, now)

        result = database.cleanup_lifecycle(
            reference_time=now,
            processed_message_retention_days=30,
            rejected_message_retention_days=30,
            dead_letter_retention_days=30,
            freight_record_retention_days=30,
        )
        assert result["deleted"] == {
            "freight_records": 1,
            "processed_messages": 1,
            "rejected_messages": 1,
            "dead_letters": 1,
        }, result
        assert result["remaining"]["freight_records"] == 2
        assert result["remaining"]["processed_messages"] == 1
        assert result["remaining"]["rejected_messages"] == 1
        assert result["remaining"]["dead_letters"] == 1
        assert result["remaining"]["pending_events"] == 1
        assert database.cleanup_lifecycle(
            reference_time=now,
            processed_message_retention_days=30,
            rejected_message_retention_days=30,
            dead_letter_retention_days=30,
            freight_record_retention_days=30,
        )["deleted_total"] == 0

        with database.session() as connection:
            connection.execute(
                "INSERT INTO processed_messages VALUES ('permanent-old', ?)",
                ("2020-01-01T00:00:00+00:00",),
            )
        permanent = database.cleanup_lifecycle(
            reference_time=now,
            processed_message_retention_days=0,
            rejected_message_retention_days=0,
            dead_letter_retention_days=0,
            freight_record_retention_days=0,
        )
        assert permanent["deleted_total"] == 0

        backup_dir = os.path.join(directory, "group", "备份")
        os.makedirs(backup_dir, exist_ok=True)
        for day in ("2026-06-01", "2026-08-10", "2026-08-19"):
            open(os.path.join(backup_dir, f"{day}_物流运价备份.zip"), "wb").close()
        assert prune_daily_backups(
            os.path.join(directory, "group"), 30, date(2026, 8, 19)
        ) == 1

        history_rows = [
            {"日期": "2026-06-01", "始发地": "南宁", "目的城市": "佛山", "货物": "板材", "平均运价": 230, "最低运价": 230, "最高运价": 230, "报价数量": 1},
            {"日期": "2026-08-10", "始发地": "南宁", "目的城市": "佛山", "货物": "板材", "平均运价": 235, "最低运价": 235, "最高运价": 235, "报价数量": 1},
        ]
        filtered_history = merge_daily_history(history_rows, date(2026, 7, 20))
        assert filtered_history["日期"].astype(str).tolist() == ["2026-08-10"]
        assert not os.path.exists(os.path.join(directory, HISTORY_DAILY_CSV))

        live_path = os.path.join(directory, "qq_live_config.json")
        rules_path = os.path.join(directory, "freight_rules.json")
        write_json(live_path, {
            "ws_url": "ws://127.0.0.1:3001",
            "group_ids": ["1", "2"],
            "group_names": {"1": "专属群", "2": "继承群"},
            "group_default_origins": {"1": "南宁", "2": "南宁"},
        })
        write_json(rules_path, {
            "default_origin": "南宁",
            "origin_groups": {"南宁": ["南宁"]},
            "destination_groups": {"佛山": ["佛山"]},
            "board_types": ["大板"],
            "price_threshold": 1000,
        })
        manager = FreightConfigurationManager(live_path, rules_path)
        snapshot = manager.snapshot()
        assert snapshot["lifecycle"]["freight_record_retention_days"] == 0
        assert snapshot["lifecycle"]["processed_message_retention_days"] == 365
        assert snapshot["group_lifecycles"] == {}
        submitted = copy.deepcopy(snapshot)
        submitted["lifecycle"].update({
            "maintenance_interval_minutes": 15,
            "processed_message_retention_days": 90,
            "rejected_message_retention_days": 60,
            "dead_letter_retention_days": 7,
            "freight_record_retention_days": 730,
            "backup_retention_days": 45,
        })
        submitted["group_lifecycles"] = {
            "1": {
                **submitted["lifecycle"],
                "maintenance_interval_minutes": 5,
                "processed_message_retention_days": 2,
                "rejected_message_retention_days": 3,
            }
        }
        assert manager.apply(submitted)["ok"]
        saved = manager.snapshot()["lifecycle"]
        assert saved["processed_message_retention_days"] == 90
        assert saved["freight_record_retention_days"] == 730
        assert saved["backup_retention_days"] == 45
        saved_overrides = manager.snapshot()["group_lifecycles"]
        assert saved_overrides["1"]["processed_message_retention_days"] == 2
        loaded = load_qq_live_config(live_path)
        profiles = build_qq_group_profiles(loaded)
        assert profiles["1"]["lifecycle_policy_source"] == "group_override"
        assert profiles["1"]["data_lifecycle"]["processed_message_retention_days"] == 2
        assert profiles["2"]["lifecycle_policy_source"] == "default_template"
        assert profiles["2"]["data_lifecycle"]["processed_message_retention_days"] == 90
        for field_id in (
            "lifecycleEnabled", "lifecycleIntervalMinutes", "freightRetentionDays",
            "processedRetentionDays", "rejectedRetentionDays",
            "deadLetterRetentionDays", "backupRetentionDays",
        ):
            assert f'id="{field_id}"' in DASHBOARD_HTML
        assert 'id="groupLifecycleRows"' in DASHBOARD_HTML
        assert "默认数据生命周期模板" in DASHBOARD_HTML
        invalid = copy.deepcopy(manager.snapshot())
        invalid["lifecycle"]["dead_letter_retention_days"] = -1
        try:
            manager.apply(invalid)
            raise AssertionError("负数保留天数本应被拒绝")
        except ValueError as exc:
            assert "死信" in str(exc)
        orphan = copy.deepcopy(manager.snapshot())
        orphan["group_lifecycles"]["999"] = dict(orphan["lifecycle"])
        try:
            manager.apply(orphan)
            raise AssertionError("不存在群的专属策略本应被拒绝")
        except ValueError as exc:
            assert "不在QQ群列表" in str(exc)

        scheduler_db = FreightDatabase(database_path)
        with scheduler_db.session() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO rejected_messages(
                    rejection_key, reason, created_at
                ) VALUES ('scheduler-old', 'bad', '2020-01-01T00:00:00+00:00')
                """
            )
        status = RuntimeStatus(os.path.join(directory, "status"))
        refreshes = []
        lifecycle = DataLifecycleManager(
            {"1": {
                "group_id": "1",
                "group_name": "测试",
                "output_dir": os.path.dirname(database_path),
                "database_file": database_path,
            }},
            status,
            {
                "enabled": True,
                "maintenance_interval_minutes": 60,
                "processed_message_retention_days": 0,
                "rejected_message_retention_days": 30,
                "dead_letter_retention_days": 0,
                "freight_record_retention_days": 0,
                "backup_retention_days": 30,
            },
            refresh_callback=lambda group_id, force_full=False: refreshes.append(
                (group_id, force_full)
            ),
            interval_seconds=0.05,
        )
        deadline = time.time() + 3
        lifecycle_snapshot = {}
        while time.time() < deadline:
            lifecycle_snapshot = status.snapshot().get("groups", {}).get("1", {})
            if lifecycle_snapshot.get("lifecycle_last_run_at"):
                break
            time.sleep(0.02)
        assert lifecycle_snapshot.get("lifecycle_state") == "ready", lifecycle_snapshot
        assert lifecycle_snapshot.get("lifecycle_deleted_total", 0) >= 1
        assert ("1", True) in refreshes
        assert lifecycle.stop(timeout=2)
        status.close()

    print(json.dumps({
        "per_table_retention": True,
        "zero_means_forever": True,
        "pending_queue_preserved": True,
        "future_freight_preserved": True,
        "backup_retention": True,
        "configuration_validation": True,
        "automatic_per_group_cleanup": True,
        "excel_refresh_after_report_cleanup": True,
        "derived_history_retention": True,
        "management_page_configuration": True,
        "new_group_inherits_default_template": True,
        "group_specific_override": True,
        "orphan_override_rejected": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
