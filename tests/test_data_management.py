import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import qq_freight_parser as parser
from freight_data import DataConflictError, FreightDataService
from freight_runtime import FreightDatabase, RuntimeStatus, start_status_dashboard


def create_demo(directory):
    root = Path(directory)
    profiles = {}
    for group_id, name in (("1001", "南宁运价演示群"), ("1002", "贵港独立演示群")):
        output = root / group_id
        output.mkdir(parents=True, exist_ok=True)
        profiles[group_id] = {"group_id": group_id, "group_name": name,
                              "output_dir": str(output), "database_file": str(output / "运价数据.db"),
                              "input_file": str(output / parser.INPUT_FILE), "default_origin": "南宁",
                              "data_lifecycle": {}, "backup_enabled": False}
    today = parser.trusted_today()
    records = []
    for index, (day, origin, city, price) in enumerate([
        (today, "南宁", "佛山", 100), (today, "南宁", "佛山", 200),
        (today, "贵港", "杭州", 300), (today + timedelta(days=2), "南宁", "佛山", 150),
        (today, "南宁", "佛山", 1200), (today - timedelta(days=400), "南宁", "佛山", 80),
    ]):
        record = parser.parse_freight_line(f"{origin}到{city} 大板 {price}", day.isoformat(), "测试发送人")
        assert record is not None
        records.append(record)
    db = FreightDatabase(profiles["1001"]["database_file"])
    db.insert_records([(parser.build_record_content_dedup_key(r), r) for r in records])
    db.add_rejection("reject-1", "message-1", "1001", "测试用户", today.isoformat(),
                     "无报价", "<img src=x onerror=alert(1)> 找车")
    other = FreightDatabase(profiles["1002"]["database_file"])
    other.insert_records([(parser.build_record_content_dedup_key(records[0]), records[0])])
    status = RuntimeStatus(str(root / "status"))
    service = FreightDataService(profiles, status)
    coordinator = parser.ExcelRebuildCoordinator(profiles, status, batch_delay_seconds=0, full_refresh_seconds=1)
    service.refresh_callback = coordinator.request
    return profiles, records, service, status, coordinator


class DataManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="freight-data-test-", ignore_cleanup_errors=True)
        self.profiles, self.records, self.service, self.status, self.coordinator = create_demo(self.temp.name)
        self.db = FreightDatabase(self.profiles["1001"]["database_file"])

    def tearDown(self):
        self.coordinator.stop(timeout=60)
        self.status.close()
        self.temp.cleanup()

    def params(self, **kwargs):
        return {"group_id": "1001", **kwargs}

    def test_query_statistics_and_validation(self):
        first = self.service.query(self.params(page_size="2"))
        second = self.service.query(self.params(page_size="2", page="2"))
        self.assertEqual(first["total"], 6)
        self.assertTrue(set(r["id"] for r in first["items"]).isdisjoint(r["id"] for r in second["items"]))
        self.assertEqual(self.service.query(self.params(kind="pending"))["total"], 1)
        self.assertEqual(self.service.query(self.params(kind="rejected"))["total"], 1)
        today = parser.trusted_today().isoformat()
        result = self.service.statistics(self.params(origin="南宁", destination="佛山", start=today, end=today))
        self.assertEqual(result["quote_count"], 2)
        self.assertEqual(result["items"][0]["平均运价"], 150)
        self.assertEqual(self.service.query(self.params(q="' OR 1=1 --"))["total"], 0)
        self.assertEqual(self.service.query(self.params(min_price="100", max_price="150"))["total"], 2)
        self.assertEqual(self.service.query({"group_id": "1002"})["total"], 1)
        for bad in ({"start": "2026-99-01"}, {"page": "-1"}, {"min_price": "nan"},
                    {"min_price": "10", "max_price": "1"}, {"kind": "unsafe"}):
            with self.assertRaises(ValueError):
                self.service.query(self.params(**bad))

    def test_http_security_and_previews(self):
        server = start_status_dashboard(self.status, port=0, data_service=self.service)
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base) as response:
                html = response.read().decode()
                csrf = response.headers["X-Freight-CSRF-Token"]
                self.assertIn('id="dataView"', html)
                self.assertIn('id="dataDeleteDialog"', html)
            key = parser.build_record_content_dedup_key(self.records[0])
            body = json.dumps({"group_id": "1001", "type": "freight", "ids": [key]}).encode()
            for headers in ({}, {"Origin": "https://attacker.invalid", "X-Freight-CSRF": csrf}):
                request = urllib.request.Request(base + "/api/data/delete-preview", data=body,
                    headers={"Content-Type": "application/json", **headers}, method="POST")
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            request = urllib.request.Request(base + "/api/data/delete-preview", data=body,
                headers={"Content-Type": "application/json", "Origin": base, "X-Freight-CSRF": csrf}, method="POST")
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)["count"], 1)
            self.assertEqual(self.db.count_records(), 6)
            with self.assertRaises(ValueError):
                self.service.file_path(self.params(path="../qq_live_config.json"))
            with self.assertRaises(ValueError):
                self.service.file_path(self.params(path="运价数据.db"))
        finally:
            server.shutdown()
            server.server_close()

    def test_delete_concurrency_conflict_and_reports(self):
        # Produce real Excel/CSV/PNG and past-year archives before removing data.
        parser.rebuild_qq_group_output(self.profiles["1001"])
        old_archive_files = [f["path"] for f in self.service.files(self.params())["items"] if "春节年度" in f["path"]]
        self.assertTrue(old_archive_files)
        key = parser.build_record_content_dedup_key(self.records[0])
        preview = self.service.preview_delete({"group_id": "1001", "type": "freight", "ids": [key]})
        results = []
        def remove():
            try:
                results.append(self.service.delete({"token": preview["token"], "confirm": True})["deleted"])
            except DataConflictError:
                results.append("conflict")
        threads = [threading.Thread(target=remove) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertCountEqual(results, [1, "conflict"])
        self.assertTrue(self.coordinator.wait_idle(60))
        self.assertEqual(self.db.count_records(), 5)
        self.assertEqual(FreightDatabase(self.profiles["1002"]["database_file"]).count_records(), 1)
        # A new message with the same content and an old queued append must not restore deletion.
        self.assertEqual(self.db.insert_records([(key, self.records[0])])[0], 0)
        self.coordinator.request("1001", records=[self.records[0]])
        self.assertTrue(self.coordinator.wait_idle(60))
        today = parser.trusted_today().isoformat()
        self.assertEqual(self.service.statistics(self.params(origin="南宁", destination="佛山", start=today, end=today))["items"][0]["平均运价"], 200)
        preview_sheet = self.service.preview_file(self.params(path=parser.REPORT_XLSX, sheet="报价明细"))
        self.assertEqual(preview_sheet["total"], 3)
        self.assertFalse(self.service.report_state("1001")["pending"])
        # Delete all remaining data, including the last record in a past-year archive.
        ids = [row["id"] for row in self.service.query(self.params())["items"]]
        preview = self.service.preview_delete({"group_id": "1001", "type": "freight", "ids": ids})
        self.service.delete({"token": preview["token"], "confirm": True})
        self.assertTrue(self.coordinator.wait_idle(60))
        Path(self.profiles["1001"]["input_file"]).write_text("南宁到佛山 大板 100", encoding="utf-8")
        self.assertEqual(parser.initialize_group_database(self.profiles["1001"]).count_records(), 0)
        self.assertEqual(self.service.preview_file(self.params(path=parser.REPORT_XLSX, sheet="报价明细"))["total"], 0)
        for path in old_archive_files:
            if path.endswith(parser.DETAIL_CSV):
                self.assertEqual(self.service.preview_file(self.params(path=path))["total"], 0, path)
        self.assertFalse(any(f["type"] == "png" for f in self.service.files(self.params())["items"]))
        self.assertFalse(self.service.report_state("1001")["pending"])
        with self.db.session() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM deleted_data").fetchone()[0], 6)


if __name__ == "__main__":
    unittest.main()
