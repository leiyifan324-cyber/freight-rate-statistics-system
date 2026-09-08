"""Validate public first-install defaults without connecting to QQ."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import qq_freight_parser as parser
from freight_runtime import FreightConfigurationManager


class DefaultBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='freight-defaults-')
        self.root=Path(self.tmp.name)
        self.live=self.root/'qq_live_config.json'
        self.rules=self.root/'freight_rules.json'
        shutil.copyfile(ROOT/'config'/'qq_live_config.example.json',self.live)
        shutil.copyfile(ROOT/'config'/'freight_rules.default.json',self.rules)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_pair_validation_and_relative_paths(self):
        config=parser.load_qq_live_config(str(self.live), require_group_ids=False)
        self.assertEqual(config['group_ids'],set())
        self.assertEqual(config['group_default_origins'],{})
        self.assertEqual(config['group_excluded_origins'],{})
        self.assertEqual(Path(config['output_root']),self.root/'data')
        self.assertEqual(Path(config['rules_file']),self.rules)
        self.assertEqual(parser.normalize_origin('武鸣'),'南宁')
        self.assertEqual(set(parser.ORIGIN_GROUPS),{'贵港','南宁'})
        self.assertEqual(len(parser.DEST_GROUPS),10)
        self.assertEqual(parser.DEFAULT_CARGO_SUBCATEGORY,'大板')
        self.assertEqual(parser.DEFAULT_CARGO_CATEGORY,'板材')
        self.assertEqual(parser.PRICE_THRESHOLD,1000)
        self.assertEqual(config['data_lifecycle']['freight_record_retention_days'],0)
        self.assertEqual(config['data_lifecycle']['backup_retention_days'],7)

    def test_config_roundtrip_through_management_validation(self):
        manager=FreightConfigurationManager(str(self.live),str(self.rules))
        first=manager.snapshot()
        self.assertEqual(first['groups'], [])
        first['groups']=[{'id':'10001','name':'合成测试群','default_origin':'贵港','excluded_origins':['南宁']}]
        manager.apply(first)
        before=manager.snapshot()
        manager.apply(before)  # Writes only temporary files, never live files or sockets.
        after=manager.snapshot()
        before.pop('version')
        after.pop('version')
        self.assertEqual(before,after)

    def test_no_credentials_runtime_state_or_business_data_in_defaults(self):
        live=json.loads(self.live.read_text(encoding='utf-8'))
        rules=json.loads(self.rules.read_text(encoding='utf-8'))
        self.assertEqual(live['access_token'],'')
        self.assertEqual(live['group_ids'],[])
        self.assertEqual(live['group_names'],{})
        self.assertFalse(live['ocr_enabled'])
        self.assertNotIn('_config_generation',live)
        self.assertNotIn('_config_generation',rules)
        self.assertEqual(live['status_dashboard']['host'],'127.0.0.1')
        for field in ('output_root','rules_file','ocr_script'):
            self.assertFalse(Path(live[field]).is_absolute())
        self.assertFalse(list(self.root.rglob('*.db')))
        self.assertNotIn('start_day',json.dumps(live))

    def test_actual_packaging_uses_baseline_and_preserves_existing_config(self):
        build=(ROOT/'scripts'/'build_release.ps1').read_text(encoding='utf-8')
        installer=(ROOT/'installer'/'FreightQuoteSystem.iss').read_text(encoding='utf-8')
        self.assertIn('config\\qq_live_config.example.json',build)
        self.assertIn('config\\freight_rules.default.json',build)
        for name in ('qq_live_config.json','freight_rules.json'):
            line=next(line for line in installer.splitlines() if line.startswith('Source:') and ('\\'+name+'"') in line)
            self.assertIn('onlyifdoesntexist',line)


if __name__=='__main__':unittest.main(verbosity=2)
