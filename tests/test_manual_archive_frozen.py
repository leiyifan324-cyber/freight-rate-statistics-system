"""Run a built exe against synthetic localhost NapCat, with disposable D-drive data."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from test_websocket_reconnect_integration import FakeNapCat, free_port, event, request_json_response


def main():
    exe=Path(sys.argv[1]).resolve()
    assert exe.is_file()
    expected_version=(Path(__file__).resolve().parents[1]/'VERSION').read_text(encoding='utf-8').strip()
    root=Path(os.environ.get('FREIGHT_ARCHIVE_TEST_OUT',r'D:\FreightQuoteSystem-manual-archive-20260908'))
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='frozen-year-',dir=root,ignore_cleanup_errors=True) as directory:
        work=Path(directory)
        mock=FakeNapCat()
        mock.start()
        port=free_port()
        assert port!=8765
        base=f'http://127.0.0.1:{port}'
        config={'ws_url':f'ws://127.0.0.1:{mock.port}','group_ids':['1001','1002'],
                'group_names':{'1001':'隔离甲','1002':'隔离乙'},
                'group_default_origins':{'1001':'南宁','1002':'南宁'},
                'output_root':str(work/'data'),
                'rules_file':str(Path(__file__).resolve().parents[1]/'config'/'freight_rules.default.json'),
                'ocr_enabled':False,'rebuild_on_start':False,'napcat_launcher':'',
                'data_lifecycle':{'enabled':False},'time_check_urls':['http://127.0.0.1:9'],
                'status_dashboard':{'enabled':True,'host':'127.0.0.1','port':port},
                'reconnect_seconds':1,'excel_batch_seconds':.2,'excel_full_refresh_seconds':10}
        config_path=work/'qq_live_config.json'
        config_path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
        process_log=(work/'process.log').open('ab',buffering=0)
        def launch():
            return subprocess.Popen([str(exe),'--mode','qq-live','--config',str(config_path)],
                                    cwd=work,creationflags=0x08000000,stdout=process_log,stderr=subprocess.STDOUT)
        proc=launch()
        def get(path):
            with urllib.request.urlopen(base+path,timeout=5) as r:return json.load(r)
        def wait(predicate,description,timeout=50):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                assert proc.poll() is None,'Frozen app exited'
                try:
                    value=predicate()
                    if value:return value
                except OSError:
                    pass
                time.sleep(.2)
            raise AssertionError('Timed out: '+description)
        def state():return get('/api/data/archive-status')
        def idle():
            groups=get('/api/status')['groups'].values()
            return all(g.get('queue_depth',0)==0 and g.get('queue_state')!='processing' for g in groups)
        def post(action,**body):
            wait(idle,'queue drain')
            payload={'version':state()['version'],'confirm':True,**body}
            code,result=request_json_response(base+'/api/data/'+action,method='POST',payload=payload)
            assert code==200,(action,code,result)
            return result
        try:
            health=wait(lambda:get('/api/health'),'health')
            assert health['version']==expected_version
            wait(lambda:mock.connection_count>=2,'mock reconnect')
            assert state()['mode']=='legacy'
            yesterday=date.today()-timedelta(days=1)
            past=event(1001,'year-past','南宁到佛山大板123')
            past['time']=datetime.combine(yesterday,datetime.min.time()).timestamp()+12*3600
            mock.send_event(past)
            wait(lambda:get('/api/data/records?group_id=1001')['total']>=3,'past record')
            post('archive-open',name='隔离年度一',start_day=yesterday.isoformat())
            wait(lambda:(work/'data'/'QQ群_1001'/'手动年度统计'/'当前'/'年度统计汇总.xlsx').exists(),'current Excel')
            post('archive-close',end_day=yesterday.isoformat())
            wait(lambda:state()['current']['state']=='closed','fixed archive')
            details=get('/api/data/archive-details?cycle_id=1')
            assert details['groups'][0]['count']==1,details
            assert details['groups'][0]['annual'][0]['平均运价']==123,details
            saved=work/'data'/'QQ群_1001'/'手动年度归档'/'1'/'年度统计汇总.xlsx'
            digest=hashlib.sha256(saved.read_bytes()).hexdigest()
            before=get('/api/data/records?group_id=1001')['total']
            mock.send_event(event(1001,'year-pause','南宁到佛山大板321'))
            wait(lambda:get('/api/data/records?group_id=1001')['total']==before+1,'paused raw receipt')
            assert get('/api/data/statistics?group_id=1001')['quote_count']==0
            post('archive-open',name='隔离年度二',start_day=date.today().isoformat())
            assert get('/api/data/statistics?group_id=1001')['quote_count']==0
            mock.send_event(event(1001,'year-new','南宁到佛山大板456'))
            wait(lambda:get('/api/data/statistics?group_id=1001')['quote_count']==1,'new year quote')
            assert get('/api/data/statistics?group_id=1001')['items'][0]['平均运价']==456
            assert hashlib.sha256(saved.read_bytes()).hexdigest()==digest
            proc.terminate()
            proc.wait(timeout=15)
            proc=launch()
            wait(lambda:get('/api/health')['connection']=='connected','restart connected')
            assert state()['current']['name']=='隔离年度二'
            assert get('/api/data/statistics?group_id=1001')['quote_count']==1
            assert hashlib.sha256(saved.read_bytes()).hexdigest()==digest
            report={'status':'passed','version':expected_version,'archive_rows':1,'year2_quote_count':1,
                    'pause_excluded':True,'restart_persisted':True,'archive_unchanged':True,
                    'production_data_accessed':False}
            (root/'frozen-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
        except Exception:
            print('FROZEN_EXIT',proc.poll(),flush=True)
            for log in work.rglob('*.log'):
                print(log.name, log.read_text(encoding='utf-8',errors='replace')[-8000:],flush=True)
            raise
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
            mock.stop()
            process_log.close()


if __name__=='__main__':main()
