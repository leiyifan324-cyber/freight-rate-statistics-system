"""Isolated pause admission, fixed chart snapshots, stale detection, HTTP guards."""
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import qq_freight_parser as parser
import freight_daily
from freight_collection import CollectionControl
from freight_data import FreightDataService, DataConflictError
from freight_runtime import FreightDatabase, RuntimeStatus, start_status_dashboard


class DailyControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='freight-daily-')
        self.root = Path(self.tmp.name)
        self.profiles = {}
        for key in ('1001', '1002'):
            folder = self.root/key
            folder.mkdir()
            self.profiles[key] = {'group_id': key, 'group_name': '运价测试群' + key,
                'output_dir': str(folder), 'database_file': str(folder/'运价数据.db'),
                'input_file': str(folder/parser.INPUT_FILE), 'default_origin': '南宁',
                'data_lifecycle': {}, 'backup_enabled': False}
            FreightDatabase(self.profiles[key]['database_file'])
        self.status = RuntimeStatus(str(self.root))
        self.service = FreightDataService(self.profiles, self.status)
        self.service.refresh_callback = lambda *args, **kwargs: True
        self.control = self.service.collection
        self.day = parser.trusted_today().isoformat()
        self.selection = {'group_id': '1001', 'day': self.day}
        self.service.archive.open({'name': '日报测试年度', 'start_day': self.day, 'version': 0, 'confirm': True})

    def tearDown(self):
        self.join()
        self.status.close()
        self.tmp.cleanup()

    def join(self):
        if self.service.daily._worker:
            self.service.daily._worker.join(30)
            self.assertFalse(self.service.daily._worker.is_alive())

    def add(self, price, destination='佛山', group='1001', day=None):
        record = parser.parse_freight_line(f'南宁到{destination} 大板 {price}', day or self.day, '隔离测试')
        key = parser.build_record_content_dedup_key(record)
        FreightDatabase(self.profiles[group]['database_file']).insert_records([(key, record)])
        return key

    def change(self, enabled):
        return self.control.change({'enabled': enabled, 'version': self.control.snapshot()['version'], 'confirm': True})

    def test_pause_persistence_and_late_history(self):
        with patch('freight_collection.time.time', return_value=100):
            self.assertEqual(self.change(False)['state'], 'paused')
        gate = CollectionControl(self.root)
        self.assertFalse(gate.snapshot()['enabled'])
        event = {'post_type': 'message', 'group_id': '1001', 'time': 102}
        self.assertTrue(gate.submit(event, lambda: self.fail('Paused event reached ingest'))['collection_paused'])
        with patch('freight_collection.time.time', return_value=110):
            self.change(True)
        self.assertTrue(self.control.submit(event, lambda: self.fail('Pause history replayed'))['collection_paused'])
        self.assertEqual(self.control.submit({**event, 'time': 111}, lambda: {'ok': True}), {'ok': True})
        with self.assertRaises(DataConflictError):
            self.control.change({'enabled': False, 'version': 0, 'confirm': True})

    def test_admission_stop_race_waits_for_enqueue(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        def enqueue():
            entered.set()
            release.wait(5)
            return {'queued': True}
        thread = threading.Thread(target=lambda: self.control.submit({'post_type': 'message'}, enqueue))
        thread.start()
        self.assertTrue(entered.wait(3))
        stopper = threading.Thread(target=lambda: (self.change(False), stopped.set()))
        stopper.start()
        self.assertFalse(stopped.wait(.1))
        release.set()
        thread.join(5)
        stopper.join(5)
        self.assertTrue(stopped.is_set())
        self.assertFalse(self.control.snapshot()['enabled'])

    def test_actual_processor_drains_and_restarts_admission(self):
        actual = parser.OneBotFreightIngestor(self.profiles)
        entered, release = threading.Event(), threading.Event()
        original = actual.ingest
        def delayed(event):
            entered.set()
            release.wait(5)
            return original(event)
        actual.ingest = delayed
        processor = parser.GroupMessageProcessor(self.profiles, actual, self.status, collection_control=self.control)
        self.control.pending_count = lambda: sum(db.count_pending_inbound_events() for db in processor._databases.values())
        def event(price, key):
            return {'post_type': 'message', 'message_type': 'group', 'group_id': 1001,
                    'message_id': key, 'time': time.time(), 'sender': {'nickname': '验证'},
                    'message': [{'type': 'text', 'data': {'text': f'南宁到佛山大板{price}'}}]}
        try:
            processor.submit(event(100, 'before'))
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.change(False)['state'], 'pausing')
            self.assertTrue(processor.submit(event(200, 'during'))['collection_paused'])
            release.set()
            self.assertTrue(processor.wait_idle(10))
            self.assertEqual(self.control.snapshot()['state'], 'paused')
            self.assertEqual(actual.databases['1001'].count_records(), 1)
            self.change(True)
            processor.submit(event(300, 'after'))
            self.assertTrue(processor.wait_idle(10))
            self.assertEqual(actual.databases['1001'].count_records(), 2)
        finally:
            release.set()
            processor.stop(timeout=10)

    def test_png_excel_same_snapshot_and_delete_stale(self):
        key = self.add(100)
        self.add(200)
        self.add(2000)
        self.add(900, day=(parser.trusted_today()+timedelta(days=1)).isoformat())
        self.change(False)
        self.service.daily.generate(self.selection)
        self.join()
        result = self.service.daily.status(self.selection)
        self.assertEqual(result['job']['state'], 'ready', result)
        report = result['reports'][0]
        self.assertEqual(report['quote_count'], 2)
        self.assertEqual(report['rows'][0]['平均运价'], 150)
        self.assertFalse(report['stale'])
        folder = Path(self.profiles['1001']['output_dir']) / report['base_path']
        self.assertTrue((folder/'当日运价_01.png').read_bytes().startswith(b'\x89PNG'))
        sheet = self.service.preview_file({'group_id': '1001', 'path': report['base_path']+'/当日报价与统计.xlsx', 'sheet': '大类日均'})
        self.assertEqual(sheet['items'][0][sheet['headers'].index('平均运价')], 150)
        image_hash = hashlib.sha256((folder/'当日运价_01.png').read_bytes()).hexdigest()
        preview = self.service.preview_delete({'group_id': '1001', 'type': 'freight', 'ids': [key]})
        self.service.delete({'token': preview['token'], 'confirm': True})
        self.assertTrue(self.service.daily.status(self.selection)['reports'][0]['stale'])
        self.assertEqual(hashlib.sha256((folder/'当日运价_01.png').read_bytes()).hexdigest(), image_hash)
        self.assertFalse(self.control.snapshot()['enabled'])
        self.service.daily.generate(self.selection)
        self.join()
        reports = self.service.daily.status(self.selection)['reports']
        self.assertEqual(len(reports), 2)
        self.assertEqual(sum(not r['stale'] for r in reports), 1)

    def test_generation_freezes_before_background_drawing(self):
        self.add(100)
        entered, release = threading.Event(), threading.Event()
        draw = freight_daily.draw_daily
        def delayed(*args):
            entered.set()
            release.wait(5)
            return draw(*args)
        with patch.object(freight_daily, 'draw_daily', delayed):
            self.service.daily.generate(self.selection)
            self.assertTrue(entered.wait(3))
            self.add(300)
            release.set()
            self.join()
        report = self.service.daily.status(self.selection)['reports'][0]
        self.assertEqual(report['rows'][0]['平均运价'], 100)
        self.assertTrue(report['stale'])

    def test_selection_rules_and_no_empty_chart(self):
        self.add(100)
        self.add(300, destination='杭州')
        self.service.daily.generate({**self.selection, 'destination': '佛山'})
        self.join()
        self.assertEqual(self.service.daily.status({**self.selection, 'destination': '佛山'})['reports'][0]['quote_count'], 1)
        with patch.object(parser, 'PRICE_THRESHOLD', 50):
            self.assertTrue(self.service.daily.status({**self.selection, 'destination': '佛山'})['reports'][0]['stale'])
        for bad in ({'day': '2026-02-30'}, {'day': '2099-01-01'}, {'group_id': 'missing'}, {'origin': []}, {'destination': '无数据'}):
            with self.assertRaises(ValueError):
                self.service.daily.generate({**self.selection, **bad})
        self.control.pending_count = lambda: 1
        with self.assertRaises(DataConflictError):
            self.service.daily.generate(self.selection)

    def test_export_failure_and_restart_detection(self):
        self.add(100)
        with patch.object(freight_daily, 'draw_daily', side_effect=OSError('test disk failure')):
            self.service.daily.generate(self.selection)
            self.join()
        result = self.service.daily.status(self.selection)
        self.assertEqual(result['job']['state'], 'error')
        self.assertEqual(result['reports'], [])
        path = self.root/'1001'/freight_daily.FOLDER/'.job.json'
        freight_daily.json_write(path, {'state': 'running'})
        new = FreightDataService(self.profiles)
        self.assertEqual(new.daily.status(self.selection)['job']['state'], 'interrupted')

    def test_http_csrf_controls_health_and_html(self):
        self.status.update(connection='connected')
        server = start_status_dashboard(self.status, port=0, data_service=self.service)
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base) as r:
                csrf = r.headers['X-Freight-CSRF-Token']
                html = r.read().decode()
            for element in ('collectionStop', 'collectionStart', 'dailyGenerate'):
                self.assertIn('id="'+element+'"', html)
            for path in ('collection-change', 'daily-generate'):
                request = urllib.request.Request(base+'/api/data/'+path, data=b'{}', headers={'Content-Type': 'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            request = urllib.request.Request(base+'/api/data/collection-change',
                data=json.dumps({'enabled': False, 'version': 0, 'confirm': True}).encode(),
                headers={'Content-Type': 'application/json', 'Origin': base, 'X-Freight-CSRF': csrf})
            with urllib.request.urlopen(request) as r:
                self.assertFalse(json.load(r)['enabled'])
            with urllib.request.urlopen(base+'/api/health') as r:
                self.assertFalse(json.load(r)['accepting_messages'])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
