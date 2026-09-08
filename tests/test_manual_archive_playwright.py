"""Browser checks against an isolated support server, not production port 8765."""
import os
import tempfile
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

out = Path(os.environ.get('FREIGHT_ARCHIVE_TEST_OUT', r'D:\FreightQuoteSystem-manual-archive-20260908'))
out.mkdir(parents=True,exist_ok=True)
from test_data_management import create_demo, parser, start_status_dashboard, FreightDatabase
import freight_data_ui
import freight_archive_ui

# In-process lifetime management avoids orphaned Windows shell child servers.
temp = tempfile.TemporaryDirectory(prefix='freight-year-ui-',ignore_cleanup_errors=True)
profiles, records, service, status, coordinator = create_demo(temp.name)
database = FreightDatabase(profiles['1001']['database_file'])
extra = [parser.parse_freight_line(f'贵港到杭州 大板 {400+i}', date.today().isoformat(),'分页测试') for i in range(26)]
database.insert_records([(parser.build_record_content_dedup_key(r),r) for r in extra])
server=start_status_dashboard(status,port=0,data_service=service,
    recent_data_provider=lambda limit:parser.build_recent_data_snapshot(profiles,limit))
port=server.server_port
assert port != 8765, 'Never mutate production statistical years in browser tests'
with urllib.request.urlopen(f'http://127.0.0.1:{port}/?view=archive',timeout=5) as response:
    assert b'archiveView' in response.read()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True, executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    page=browser.new_page(viewport={'width':1440,'height':1050},accept_downloads=True)
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    pending=set()
    page.on('request',lambda r:pending.add(r.url))
    page.on('requestfinished',lambda r:pending.discard(r.url))
    page.on('requestfailed',lambda r:pending.discard(r.url))
    try:
        page.goto(f'http://127.0.0.1:{port}/?view=archive',wait_until='domcontentloaded')
        page.wait_for_load_state('networkidle',timeout=10000)
        print('BUTTONS',page.locator('button:visible').all_text_contents(),flush=True)
        page.screenshot(path=str(out/'archive-initial.png'),full_page=True)
        expect(page.locator('#archiveView')).to_be_visible()
        expect(page.locator('#yearState')).to_have_text('尚未启用手动年度')
        page.fill('#yearName','2026 测试经营年度 <b>普通文本</b>')
        page.fill('#yearStart',(date.today()-timedelta(days=410)).isoformat())
        page.click('#yearOpen')
        expect(page.locator('#yearDialog')).to_be_visible()
        expect(page.locator('#yearConfirmBody b')).to_have_count(0)
        page.click('#yearCancel')
        expect(page.locator('#yearState')).to_have_text('尚未启用手动年度')
        page.click('#yearOpen')
        page.click('#yearConfirm')
        expect(page.locator('#yearState')).to_have_text('统计中')
        page.reload(wait_until='networkidle')
        expect(page.locator('#yearCurrent')).to_contain_text('2026 测试经营年度')
        page.fill('#yearEnd',(date.today()-timedelta(days=1)).isoformat())
        page.click('#yearPreview')
        expect(page.locator('#yearConfirmBody')).to_contain_text('1 条')
        page.click('#yearCancel')
        expect(page.locator('#yearState')).to_have_text('统计中')
        page.click('#yearPreview')
        page.click('#yearConfirm')
        expect(page.locator('#yearState')).to_have_text('已归档 · 等待开启',timeout=30000)
        page.click('.year-detail-button')
        expect(page.locator('#yearDetails')).to_contain_text('关账快照')
        expect(page.locator('#yearDetails b')).to_have_count(0)
        with page.expect_download() as dl:
            page.get_by_text('下载年度Excel',exact=True).first.click()
        dl.value.save_as(str(out/'browser-archived.xlsx'))
        page.screenshot(path=str(out/'archive-closed.png'),full_page=True)
        page.click('[data-view="data"]')
        expect(page.locator('#archiveView')).to_be_hidden()
        expect(page.locator('#dataTotal')).to_have_text('32')
        page.click('[data-panel="statistics"]')
        expect(page.locator('#dataStatsNote')).to_contain_text('参与统计 0 条报价')
        expect(page.locator('#dataMessage')).to_contain_text('暂停时为空')
        page.click('[data-view="archive"]')
        page.fill('#yearName','2027 新统计年度')
        page.click('#yearOpen')
        expect(page.locator('#yearConfirmBody')).to_contain_text('开启前已入库数据不补入')
        page.click('#yearConfirm')
        expect(page.locator('#yearState')).to_have_text('统计中')
        page.screenshot(path=str(out/'archive-active.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(out/'archive-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        page.click('[data-view="status"]')
        expect(page.locator('#archiveView')).to_be_hidden()
        page.click('[data-view="config"]')
        expect(page.locator('#archiveView')).to_be_hidden()
        assert not errors,errors
        print('MANUAL_ARCHIVE_UI_OK: first/open/cancel/confirm/close/pause/download/reopen/reload/XSS/mobile/tabs',flush=True)
    except Exception:
        print('UI_ERRORS',errors,'PENDING',pending,flush=True)
        page.screenshot(path=str(out/'archive-failure.png'),full_page=True)
        raise
    finally:
        browser.close()
        if service.archive._worker:service.archive._worker.join(30)
        server.shutdown()
        server.server_close()
        coordinator.stop(timeout=60)
        status.close()
        temp.cleanup()
