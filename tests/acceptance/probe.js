// 页面结构探查脚本（调试工具，不属于验收用例）
// 用法：node probe.js —— 登录平台后逐页访问，dump 关键 DOM 结构到 stdout
// 目的：为验收用例提供真实选择器依据（不凭想象写 selector）
const { chromium } = require('@playwright/test');
const { baseUrl, admin } = require('./config');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const out = (title, text) => console.log(`\n===== ${title} =====\n` + String(text).slice(0, 1200));

  // ---- 登录页 ----
  await page.goto(baseUrl + '/');
  await page.waitForLoadState('networkidle');
  out('URL after goto /', page.url());
  const inputs = await page.locator('input').evaluateAll(els =>
    els.map(e => ({ placeholder: e.placeholder, type: e.type, name: e.name, id: e.id })));
  out('login inputs', JSON.stringify(inputs, null, 1));
  const buttons = await page.locator('button').evaluateAll(els => els.map(e => e.textContent.trim()));
  out('login buttons', JSON.stringify(buttons));

  // ---- 登录 ----
  await page.locator('input').first().fill(admin.username);
  await page.locator('input[type="password"]').fill(admin.password);
  await page.locator('button').last().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1500);
  out('URL after login', page.url());
  // 安全警告弹窗？
  const dialogText = await page.locator('.ant-modal, [class*="modal"]').allInnerTexts().catch(() => []);
  out('modal after login', JSON.stringify(dialogText).slice(0, 500));
  const modalBtns = await page.locator('.ant-modal button, [class*="modal"] button').allTextContents().catch(() => []);
  out('modal buttons', JSON.stringify(modalBtns));
  // 点掉弹窗
  for (const b of await page.locator('.ant-modal button, [class*="modal"] button').all()) {
    const t = (await b.textContent() || '').trim();
    if (t.includes('知道') || t.includes('OK') || t.includes('确定')) { await b.click(); break; }
  }
  await page.waitForTimeout(800);

  // ---- 逐页 dump ----
  for (const path of ['/home', '/host', '/exec/task', '/ssh', '/deploy/request', '/monitor', '/alarm/alarm']) {
    await page.goto(baseUrl + path);
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(1200);
    const title = await page.title();
    const bodyText = await page.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n'));
    out(`${path} (title=${title})`, bodyText);
  }

  await browser.close();
})().catch(e => { console.error('PROBE FAILED:', e.message); process.exit(1); });
