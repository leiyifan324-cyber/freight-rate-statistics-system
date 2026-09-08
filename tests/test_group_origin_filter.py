"""Per-group exclusions use the parsed origin, never keywords from the whole message."""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import qq_freight_parser as parser
from freight_runtime import FreightConfigurationManager

GG, NN, OTHER = '10001', '10002', '10003'
FILTERS = {GG: ['南宁'], NN: ['贵港']}


class GroupOriginFilterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='freight-origin-filter-')
        self.root = Path(self.temp.name)
        self.live = self.root / 'qq_live_config.json'
        self.rules = self.root / 'freight_rules.json'
        shutil.copyfile(ROOT / 'config/qq_live_config.example.json', self.live)
        shutil.copyfile(ROOT / 'config/freight_rules.default.json', self.rules)
        self.raw = json.loads(self.live.read_text(encoding='utf-8'))
        self.raw.update(json.loads((ROOT/'tests/fixtures/synthetic_groups.json').read_text(encoding='utf-8')))
        self.raw['group_excluded_origins'] = copy.deepcopy(FILTERS)
        self.save_raw()
        self.manager = FreightConfigurationManager(str(self.live), str(self.rules))

    def tearDown(self):
        self.temp.cleanup()

    def save_raw(self):
        self.live.write_text(json.dumps(self.raw, ensure_ascii=False), encoding='utf-8')

    def ingestor(self):
        config = parser.load_qq_live_config(str(self.live))
        return parser.OneBotFreightIngestor(parser.build_qq_group_profiles(config))

    def event(self, group, text, message_id='test'):
        return {'post_type': 'message', 'message_type': 'group', 'group_id': int(group),
                'message_id': message_id, 'time': datetime.now().timestamp(),
                'sender': {'nickname': '隔离验证'},
                'message': [{'type': 'text', 'data': {'text': text}}]}

    def test_01_explicit_alias_default_and_mixed_lines(self):
        ingestor = self.ingestor()
        cases = {
            GG: ('贵港到佛山大板120元/吨\n佛山大板150元/吨\n覃塘区佛山大板180元/吨\n'
                 '南宁到佛山大板300元/吨\n武鸣区到佛山大板330元/吨', '贵港', 150, 2),
            NN: ('南宁到佛山大板210元/吨\n佛山大板240元/吨\n武鸣佛山大板270元/吨\n'
                 '贵港到佛山大板390元/吨\n港北区到佛山大板420元/吨\n覃塘到佛山大板450元/吨', '南宁', 240, 3),
        }
        for group, (text, origin, expected, rejected) in cases.items():
            with self.subTest(group=group):
                result = ingestor.ingest(self.event(group, text))
                self.assertEqual(result['inserted_count'], 3, result)
                self.assertEqual(result['rejected_line_count'], rejected, result)
                records = ingestor.databases[group].fetch_records()
                self.assertEqual({r['始发地'] for r in records}, {origin})
                self.assertEqual(parser.summarize_daily_quote(records)[0]['平均运价'], expected)
                for rejection in ingestor.databases[group].fetch_rejections():
                    self.assertIn('本群不统计始发地', rejection['原因'])

    def test_other_group_remains_unrestricted(self):
        ingestor = self.ingestor()
        result = ingestor.ingest(self.event(OTHER, '南宁到佛山大板120\n贵港到佛山大板150'))
        self.assertEqual(result['inserted_count'], 2)

    def test_future_exclusion_and_price_guards(self):
        ingestor = self.ingestor()
        result = ingestor.ingest(self.event(GG,
            '明天南宁到佛山大板120\n贵港到佛山大板40元/方\n贵港到佛山大板-987'))
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(ingestor.databases[GG].fetch_records(), [])
        self.assertEqual(len(ingestor.databases[GG].fetch_rejections()), 3)

    def test_old_data_not_reclassified_and_legacy_config_supported(self):
        self.raw.pop('group_excluded_origins')
        self.save_raw()
        old = self.ingestor()
        old.ingest(self.event(GG, '南宁到佛山大板120', 'before'))
        records = old.databases[GG].fetch_records()
        self.assertEqual(len(records), 1)
        self.raw['group_excluded_origins'] = FILTERS
        self.save_raw()
        new = self.ingestor()
        self.assertEqual(new.ingest(self.event(GG, '南宁到佛山大板150', 'after'))['status'], 'rejected')
        self.assertEqual(new.databases[GG].fetch_records(), records)
        self.assertIsNotNone(parser.parse_freight_line('南宁到佛山大板150', '2026-09-08', '手动文本'))

    def test_management_roundtrip_omission_preserves_and_explicit_clear(self):
        snapshot = self.manager.snapshot()
        self.assertEqual({g['id']: g['excluded_origins'] for g in snapshot['groups'] if g['excluded_origins']}, FILTERS)
        self.manager.apply(snapshot)
        snapshot = self.manager.snapshot()
        for group in snapshot['groups']:
            group.pop('excluded_origins')  # Old browser/client must not silently disable guards.
        self.manager.apply(snapshot)
        self.assertEqual(json.loads(self.live.read_text(encoding='utf-8'))['group_excluded_origins'], FILTERS)
        snapshot = self.manager.snapshot()
        next(g for g in snapshot['groups'] if g['id'] == GG)['excluded_origins'] = []
        self.manager.apply(snapshot)
        current = parser.load_qq_live_config(str(self.live))
        self.assertNotIn(GG, current['group_excluded_origins'])
        self.assertEqual(current['group_excluded_origins'][NN], ['贵港'])

    def test_invalid_settings_fail_without_partial_save(self):
        before = (self.live.read_bytes(), self.rules.read_bytes())
        for bad in ('南宁', ['不存在'], ['贵港'], [123], None):
            with self.subTest(bad=bad):
                snapshot = self.manager.snapshot()
                next(g for g in snapshot['groups'] if g['id'] == GG)['excluded_origins'] = bad
                with self.assertRaises(ValueError):
                    self.manager.apply(snapshot)
                self.assertEqual((self.live.read_bytes(), self.rules.read_bytes()), before)
        for bad in ('南宁', ['不存在'], ['贵港'], None):
            self.raw['group_excluded_origins'] = {GG: bad}
            self.save_raw()
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                parser.load_qq_live_config(str(self.live))

    def test_removed_group_does_not_leave_stale_filter(self):
        snapshot = self.manager.snapshot()
        snapshot['groups'] = [g for g in snapshot['groups'] if g['id'] != GG]
        self.manager.apply(snapshot)
        self.assertEqual(parser.load_qq_live_config(str(self.live))['group_excluded_origins'], {NN: ['贵港']})

    def test_new_install_has_no_real_groups_or_group_exclusions(self):
        template = json.loads((ROOT / 'config/qq_live_config.example.json').read_text(encoding='utf-8'))
        self.assertEqual(template['group_ids'], [])
        self.assertEqual(template['group_excluded_origins'], {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
