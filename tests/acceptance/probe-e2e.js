// 终验探查脚本（调试工具）：巡检/网络探测/自愈相关页面 + playbook id 映射
const { chromium } = require('@playwright/test');
const { baseUrl, admin } = require('./config');

const out = (t, x) => console.log(`\n===== ${t} =====\n` + String(x).slice(0, 1800));

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

  // 菜单全量（找新页面入口）
  const menu = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.ant-menu-item, .ant-menu-submenu-title'))
      .map(e => e.innerText.trim()).filter(Boolean));
  out('menu', JSON.stringify(menu));

  // playbook 库 id 映射
  await page.goto(baseUrl + '/ansible/playbooks');
  await page.waitForTimeout(1200);
  const pbRows = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.ant-table-tbody tr')).map(tr => tr.innerText.replace(/\s+/g, ' ').slice(0, 60)));
  out('playbooks', JSON.stringify(pbRows, null, 1));

  // 巡检页（路由从菜单猜测，尝试几个）
  for (const path of ['/ansible/inspection', '/ansible/reports', '/ansible/net-probe']) {
    await page.goto(baseUrl + path);
    await page.waitForTimeout(1200);
    const heading = await page.evaluate(() => {
      const main = document.querySelector('main') || document.body;
      return main.innerText.slice(0, 500).replace(/\n{2,}/g, '\n');
    });
    out(`page ${path}`, heading);
  }

  await browser.close();
})().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
