"""Text-only regression: explicit oracle, no NapCat or production database access."""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import qq_freight_parser as freight
from test_quote_format_learning import PRICE_CASES


class DisabledOcr:
    enabled = False

    def recognize(self, source: str):
        raise AssertionError('Text-only collection must not call OCR or download an image')


class TextOnlyReliabilityTest(unittest.TestCase):
    def setUp(self):
        freight.load_rules_config(str(ROOT/'config/freight_rules.default.json'))
        self.temp = tempfile.TemporaryDirectory(prefix='freight-text-only-')
        self.addCleanup(self.temp.cleanup)
        cfg = json.loads((ROOT/'config/qq_live_config.example.json').read_text(encoding='utf-8'))
        cfg.update(json.loads((ROOT/'tests/fixtures/synthetic_groups.json').read_text(encoding='utf-8')))
        cfg['output_root'] = self.temp.name
        self.profiles = freight.build_qq_group_profiles(cfg)
        self.ingestor = freight.OneBotFreightIngestor(self.profiles, accept_self_messages=True, ocr=DisabledOcr())

    def test_missing_origin_formats_use_exact_group_default(self):
        for origin in ('贵港', '南宁'):
            for prefix in ('到', '到 ', '→', '->', ''):
                for body, price in [('福州大板181元/吨',181), ('佛山红板200/220',210), ('成都拼板165～170',167.5)]:
                    with self.subTest(origin=origin, text=prefix+body):
                        formatted, reason = freight.format_freight_line_with_reason(prefix+body, origin)
                        self.assertIsNotNone(formatted, reason)
                        r = freight.parse_freight_line(formatted, '2026-09-08', 'test', origin)
                        self.assertEqual((r['始发地'],r['平均报价']), (origin,price))

    def test_unknown_explicit_origin_never_uses_group_default(self):
        for text in ('扶绥到福州大板181', '八塘到佛山大板182', '未知地点到杭州红板183', '到郴州大板184'):
            with self.subTest(text=text):
                value, reason = freight.format_freight_line_with_reason(text, '贵港')
                self.assertIsNone(value)
                self.assertIn('不在范围', reason)
        self.assertEqual(freight.parse_freight_line('到福州大板120','2026-09-08','test','南宁')['始发地'],'南宁')

    def test_disconnected_digits_are_not_guessed(self):
        for text in ('大板71 1元/吨', '大板71 1 元 / 吨', '大板1 71元/吨',
                     '大板120 0', '大板120 2', '大板120 13元/吨', '大板71.1 1',
                     '大板120 150', '大板120/125 1'):
            with self.subTest(text=text):
                value, reason = freight.extract_price_with_reason(text)
                self.assertIsNone(value, (text,value))
                self.assertTrue(reason)
                self.assertIsNone(freight.auto_format_freight_line('南宁到佛山'+text,'南宁'))

    def test_existing_complex_formats_remain_supported(self):
        extra = [('大板711元/吨','711',711),('大板120 2装1卸','120',120),
                 ('大板120 车长9.6米','120',120),('大板120 信息费10元','120',120),
                 ('大板120 1车','120',120),('大板120 重量1.5吨','120',120),
                 ('大板120 吨位35','120',120),('大板120 车型13.75','120',120)]
        for text, token, average in PRICE_CASES+extra:
            with self.subTest(text=text):
                value, reason = freight.extract_price_with_reason(text)
                self.assertIsNotNone(value, reason)
                self.assertEqual((value['price_text'],value['price_avg']), (token,average))

    def make_event(self, group, key, segments):
        return {'post_type':'message','message_type':'group','group_id':int(group),'message_id':key,
                'time':datetime(2026,9,8,9).timestamp(),'sender':{'nickname':'text-regression'},'message':segments}

    def test_image_ignored_text_kept_without_any_ocr(self):
        image = {'type':'image','data':{'file':'http://127.0.0.1:9/never-download.png'}}
        pure = self.make_event('10001','pure-image',[image])
        result = self.ingestor.ingest(pure)
        self.assertEqual(result['status'],'ignored')
        self.assertIn('仅采集文字',result['reason'])
        self.assertEqual(self.ingestor.databases['10001'].fetch_records(),[])
        self.assertEqual(self.ingestor.databases['10001'].fetch_rejections(),[])
        mixed = self.make_event('10001','mixed-text',[image,{'type':'text','data':{'text':'到福州大板211元/吨'}}])
        result = self.ingestor.ingest(mixed)
        self.assertEqual(result['inserted_count'],1,result)
        self.assertEqual(self.ingestor.databases['10001'].fetch_records()[0]['平均报价'],211)
        self.assertEqual(self.ingestor.ingest(mixed)['status'],'duplicate')

    def test_three_groups_multiline_scope_and_price_guards(self):
        for group, origin in [('10003','南宁'),('10001','贵港'),('10002','南宁')]:
            other = '贵港' if origin=='南宁' else '南宁'
            text = '\n'.join(['到福州大板181元/吨','杭州红板200/220 35-36吨',
                              f'{other}到成都大板250元/吨','到佛山大板40元/方',
                              '到南昌大板71 1元/吨','明天到长沙大板300元/吨',
                              '到武汉大板-120元/吨','扶绥到佛山大板180'])
            result = self.ingestor.ingest(self.make_event(group,'matrix-'+group,[{'type':'text','data':{'text':text}}]))
            expected = [(origin,'福州',181,'2026-09-08'),(origin,'杭州',210,'2026-09-08'),(origin,'长沙',300,'2026-09-09')]
            if group=='10003': expected.append((other,'成都',250,'2026-09-08'))
            actual=[(r['始发地'],r['目的城市'],r['平均报价'],r['日期']) for r in self.ingestor.databases[group].fetch_records()]
            self.assertEqual(sorted(actual),sorted(expected),result)
            self.assertEqual(result['rejected_line_count'],8-len(expected))

    def test_fresh_install_is_text_only(self):
        cfg=json.loads((ROOT/'config/qq_live_config.example.json').read_text(encoding='utf-8'))
        self.assertFalse(cfg['ocr_enabled'])


if __name__=='__main__': unittest.main(verbosity=2)
