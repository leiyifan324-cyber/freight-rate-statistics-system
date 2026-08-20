const { chromium } = require('playwright');
let activeBrowser = null;

async function main() {
  const port = process.env.CARGO_UI_TEST_PORT || '8877';
  const screenshot = process.env.CARGO_UI_SCREENSHOT;
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CARGO_UI_BROWSER || undefined,
  });
  activeBrowser = browser;
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));

  await page.goto(`http://127.0.0.1:${port}/?view=config`, {
    waitUntil: 'domcontentloaded',
  });
  await page.waitForSelector('#cargoRows tr');
  const initialCargoCount = await page.locator('#cargoRows tr').count();
  if (initialCargoCount !== 3) {
    throw new Error(`初始货物小类没有完整呈现：${initialCargoCount}`);
  }

  await page.click('#addCargo');
  const cargoRow = page.locator('#cargoRows tr').last();
  await cargoRow.locator('.cargo-name').fill('木板');
  await cargoRow.locator('.cargo-aliases').fill('大板');
  await cargoRow.locator('.cargo-category').fill('木材');

  await page.click('#addCargoScope');
  const scopeRow = page.locator('#cargoScopeRows tr').last();
  await scopeRow.locator('.scope-level').selectOption('subcategory');
  await scopeRow.locator('.scope-cargo').selectOption('木板');
  await scopeRow.locator('.choose-routes').click();
  await page.waitForSelector('#routeDialog[open]');
  const routeChecks = page.locator('#routeOptions .route-check');
  if (await routeChecks.count() !== 4) throw new Error('线路矩阵数量不正确');
  await routeChecks.nth(0).check();
  await routeChecks.nth(3).check();
  await page.click('#confirmRoutes');
  if (!(await scopeRow.locator('.route-summary').innerText()).includes('2 条')) {
    throw new Error('线路选择摘要没有实时更新');
  }

  const invalidResponsePromise = page.waitForResponse(
    response => response.url().endsWith('/api/config') && response.request().method() === 'POST'
  );
  await page.click('#saveConfig');
  const invalidResponse = await invalidResponsePromise;
  if (invalidResponse.status() !== 400) throw new Error('重复货物别名没有被服务端拒绝');
  await page.waitForFunction(() => document.querySelector('#saveMessage').textContent.includes('同时属于'));

  await cargoRow.locator('.cargo-aliases').fill('木板材');
  const validResponsePromise = page.waitForResponse(
    response => response.url().endsWith('/api/config') && response.request().method() === 'POST'
  );
  await page.click('#saveConfig');
  const validResponse = await validResponsePromise;
  if (validResponse.status() !== 200) {
    throw new Error(`合法货物配置保存失败：${await validResponse.text()}`);
  }

  const saved = await page.evaluate(async () => (await fetch('/api/config')).json());
  const wood = saved.cargo_types.find(item => item.name === '木板');
  if (!wood || wood.category !== '木材' || !wood.aliases.includes('木板材')) {
    throw new Error('货物小类、大类或别名没有正确保存');
  }
  const scope = saved.cargo_route_scopes.find(
    item => item.level === 'subcategory' && item.cargo === '木板'
  );
  if (!scope || scope.routes.length !== 2) throw new Error('货物线路白名单没有正确保存');

  if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });
  if (pageErrors.length) throw new Error(`页面脚本错误：${pageErrors.join(' | ')}`);
  await browser.close();
  activeBrowser = null;
  await new Promise(resolve => setTimeout(resolve, 1300));
  console.log('CARGO_DASHBOARD_PLAYWRIGHT_OK');
}

main().catch(async error => {
  console.error(error);
  if (activeBrowser) await activeBrowser.close().catch(() => {});
  process.exitCode = 1;
});
