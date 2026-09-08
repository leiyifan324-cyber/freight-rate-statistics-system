"""Windows must not reuse another application's listening port."""
import logging
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from freight_runtime import RuntimeStatus, start_status_dashboard


class UnrelatedHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'unrelated-service')
    def log_message(self,*args):pass


class PortExclusionTest(unittest.TestCase):
    def test_occupied_port_is_rejected_and_logged(self):
        foreign=ThreadingHTTPServer(('127.0.0.1',0),UnrelatedHandler)
        threading.Thread(target=foreign.serve_forever,daemon=True).start()
        dashboard=None
        with tempfile.TemporaryDirectory() as directory:
            state=RuntimeStatus(directory)
            logger=logging.getLogger('port-test')
            try:
                with self.assertLogs(logger,level='WARNING') as captured:
                    dashboard=start_status_dashboard(state,'127.0.0.1',foreign.server_port,logger)
                    self.assertIsNone(dashboard,'A second listener unexpectedly bound the occupied port')
                self.assertIn('状态面板启动失败',' '.join(captured.output))
                self.assertTrue(state.snapshot()['errors'])
                with urllib.request.urlopen(f'http://127.0.0.1:{foreign.server_port}') as response:
                    self.assertEqual(response.read(),b'unrelated-service')
            finally:
                if dashboard:dashboard.shutdown();dashboard.server_close()
                state.close();foreign.shutdown();foreign.server_close()


if __name__=='__main__':unittest.main()
