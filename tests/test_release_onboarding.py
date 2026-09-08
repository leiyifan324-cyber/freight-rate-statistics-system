"""Public-package onboarding checks; isolated mocks never launch user processes."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import freight_app


class ReleaseOnboardingTest(unittest.TestCase):
    def test_cold_config_entry_starts_supervisor_first(self):
        calls = []
        with patch('freight_app.freight_supervisor.launch_supervisor', side_effect=lambda cfg:calls.append(('start',cfg))), \
             patch('freight_app.freight_supervisor.open_dashboard_when_ready', side_effect=lambda cfg,view:calls.append(('open',cfg,view)) or True):
            self.assertEqual(freight_app.open_dashboard('test.json'), 0)
        self.assertEqual(calls, [('start','test.json'),('open','test.json','config')])

    def test_unready_dashboard_is_not_reported_as_success(self):
        with patch('freight_app.freight_supervisor.launch_supervisor'), \
             patch('freight_app.freight_supervisor.open_dashboard_when_ready', return_value=False):
            self.assertEqual(freight_app.open_dashboard('test.json'), 2)

    def test_public_payload_has_no_real_group_or_login_state(self):
        cfg=json.loads((ROOT/'config/qq_live_config.example.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg['group_ids'], [])
        self.assertEqual(cfg['group_names'], {})
        self.assertEqual(cfg['access_token'], '')
        self.assertEqual(cfg['napcat_launcher'], 'NapCatQQ\\Start-NapCat.cmd')
        self.assertFalse(cfg['ocr_enabled'])
        self.assertNotIn('_config_generation', cfg)

    def test_installer_has_local_paths_and_human_readable_help(self):
        setup=(ROOT/'installer/FreightQuoteSystem.iss').read_text(encoding='utf-8')
        self.assertIn('GetDefaultInstallDir',setup)
        self.assertIn("Result := 'D:\\FreightQuoteSystem'",setup)
        desktop=next(line for line in setup.splitlines() if line.startswith('Name: "{autodesktop}'))
        self.assertIn('--mode start',desktop)
        self.assertIn('快速开始.html',setup)
        html=(ROOT/'installer/assets/快速开始.html').read_text(encoding='utf-8')
        for phrase in ('只采集文字','年度归档','开始采集','停止采集','生成当日图表','不包含腾讯QQ','非商业'):
            self.assertIn(phrase,html)


if __name__=='__main__': unittest.main(verbosity=2)
