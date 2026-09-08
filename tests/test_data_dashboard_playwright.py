"""Run against support_data_dashboard_server.py, never against production data."""
import os
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright, expect, TimeoutError


def main(base):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
        errors = []
        dialogs = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        try:
            pending = set()
            page.on("request", lambda request: pending.add(request.url))
            page.on("requestfinished", lambda request: pending.discard(request.url))
            page.on("requestfailed", lambda request: pending.discard(request.url))
            page.goto(base + "/?view=data", wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except TimeoutError:
                print({"pending_requests": list(pending), "errors": errors, "url": page.url}, flush=True)
                page.screenshot(path=r"D:\FreightQuoteSystem-build-20260908\ui-loading.png")
                raise
            expect(page.locator("#dataTotal")).to_have_text("32")
            expect(page.locator("#dataResults tbody tr")).to_have_count(25)
            page.click("#dataNext")
            expect(page.locator("#dataPageInfo")).to_have_text("第 2 / 2 页")
            expect(page.locator("#dataResults tbody tr")).to_have_count(7)
            page.select_option("#dataGroup", "1002")
            expect(page.locator("#dataTotal")).to_have_text("1")
            page.select_option("#dataGroup", "1001")
            expect(page.locator("#dataTotal")).to_have_text("32")
            page.fill("#dataOrigin", "南宁")
            page.fill("#dataMinPrice", "90")
            page.fill("#dataMaxPrice", "110")
            page.click("#dataSearch")
            expect(page.locator("#dataTotal")).to_have_text("1")
            page.click(".data-row-delete")
            expect(page.locator("#dataDeleteDialog")).to_be_visible()
            page.click("#dataCancelDelete")
            expect(page.locator("#dataTotal")).to_have_text("1")
            page.locator(".data-row-check").check()
            page.click("#dataDeleteSelected")
            page.click("#dataConfirmDelete")
            expect(page.locator("#dataTotal")).to_have_text("0")
            page.click("#dataReset")
            expect(page.locator("#dataTotal")).to_have_text("31")
            page.select_option("#dataKind", "rejected")
            page.click("#dataSearch")
            expect(page.locator("#dataTotal")).to_have_text("1")
            page.click("#dataResults summary")
            expect(page.locator("#dataResults details")).to_contain_text("<img src=x onerror=alert(1)>")
            assert not dialogs
            page.click(".data-row-delete")
            page.click("#dataConfirmDelete")
            expect(page.locator("#dataTotal")).to_have_text("0")
            page.click("#dataReset")
            page.click('[data-panel="statistics"]')
            expect(page.locator("#dataResults thead")).to_contain_text("平均运价")
            expect(page.locator("#dataStatsNote")).to_contain_text("报价")
            page.click('[data-panel="files"]')
            page.fill("#dataFileSearch", "物流报价汇总.xlsx")
            page.locator(".report-file").filter(has_text="物流报价汇总.xlsx").last.click()
            expect(page.locator("#dataSheet")).to_be_visible()
            page.select_option("#dataSheet", "报价明细")
            expect(page.locator("#dataReportBody thead")).to_contain_text("始发地")
            with page.expect_download() as download:
                page.get_by_text("下载原文件", exact=True).click()
            assert download.value.suggested_filename.endswith(".xlsx")
            page.fill("#dataFileSearch", "png")
            page.locator(".report-file").first.click()
            expect(page.locator("#dataReportBody img")).to_be_visible()
            page.wait_for_function("document.querySelector('#dataReportBody img').naturalWidth > 0")
            page.click('[data-panel="records"]')
            expect(page.locator("#dataTotal")).to_have_text("31")
            page.screenshot(path=os.environ.get("FREIGHT_UI_SCREENSHOT", str(Path.cwd() / "data-dashboard.png")), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.click('[data-view="status"]')
            expect(page.locator("#dataView")).to_be_hidden()
            page.click('[data-view="data"]')
            expect(page.locator("#dataView")).to_be_visible()
            assert not errors, errors
            print("DATA_DASHBOARD_UI_OK: query, pagination, group isolation, cancel/confirm deletion, XSS, statistics, worksheets, download, charts, mobile layout")
        finally:
            browser.close()


if __name__ == "__main__":
    from test_data_management import create_demo, parser, start_status_dashboard, FreightDatabase
    with tempfile.TemporaryDirectory(prefix="freight-ui-browser-", ignore_cleanup_errors=True) as directory:
        profiles, records, service, status, coordinator = create_demo(directory)
        database = FreightDatabase(profiles["1001"]["database_file"])
        extra = [parser.parse_freight_line(f"贵港到杭州 大板 {400+i}", parser.trusted_today().isoformat(), "分页测试") for i in range(26)]
        database.insert_records([(parser.build_record_content_dedup_key(r), r) for r in extra])
        parser.rebuild_qq_group_output(profiles["1001"])
        server = start_status_dashboard(status, port=0, data_service=service,
                                        recent_data_provider=lambda limit: parser.build_recent_data_snapshot(profiles, limit))
        try:
            main(f"http://127.0.0.1:{server.server_port}")
        finally:
            server.shutdown()
            server.server_close()
            coordinator.stop(timeout=60)
            status.close()
