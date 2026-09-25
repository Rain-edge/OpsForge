// 交互流程探查第三轮：专攻批量执行（选 7 台主机 → 执行 → 结果呈现）
const { chromium } = require('@playwright/test');
const { baseUrl, admin } = require('./config');

const out = (t, x) => console.log(`\n===== ${t} =====\n` + String(x).slice(0, 3000));

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  await page.goto(baseUrl + '/');
  await page.waitForLoadState('networkidle');
  await page.locator('#username').fill(admin.username);
  await page.locator('#password').fill(admin.password);
  await page.locator('button:has-text("登 录")').click();
  await page.waitForTimeout(1500);
  await page.locator('.ant-modal button:has-text("知道了")').first().click().catch(() => {});

  await page.goto(baseUrl + '/exec/task');
  await page.waitForTimeout(1000);
  await page.locator('button:has-text("添加目标主机")').click();
  await page.waitForTimeout(800);

  // 逐分组勾选主机：点树分组名切换右侧列表，再勾行 checkbox
  const modal = page.locator('.ant-modal:visible, .ant-modal-wrap:visible').last();
  for (const group of ['cache', 'db', 'web']) {
    await modal.locator(`text=${group}`).first().click(); // 树节点
    await page.waitForTimeout(500);
    const rowCbs = modal.locator('.ant-table-tbody .ant-checkbox-input');
    const n = await rowCbs.count();
    for (let i = 0; i < n; i++) {
      if (!(await rowCbs.nth(i).isChecked())) await rowCbs.nth(i).check();
    }
    console.log(`group ${group}: checked ${n} rows`);
  }
  await modal.locator('button:has-text("确 定")').click();
  await page.waitForTimeout(800);

  // dump 执行页全文（看已选主机怎么呈现）
  const bodyText = await page.evaluate(() => document.body.innerText);
  out('exec page after picking hosts', bodyText);

  // 填命令执行
  await page.locator('.ace_content').click();
  await page.keyboard.type('hostname');
  await page.screenshot({ path: 'probe-results/before-exec.png', fullPage: true });
  await page.locator('button:has-text("开始执行")').click();
  // 结果是实时流：等输出稳定
  await page.waitForTimeout(8000);
  await page.screenshot({ path: 'probe-results/after-exec.png', fullPage: true });
  const after = await page.evaluate(() => document.body.innerText);
  out('exec page after run', after);

  await browser.close();
})().catch(e => { console.error('PROBE3 FAILED:', e.message); process.exit(1); });
