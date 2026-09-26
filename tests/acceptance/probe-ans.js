// ANS 模块页面结构探查脚本（调试工具，非验收用例）
// 重点：Ansible 菜单组路由、exec 页主机选择控件（antd Select 虚拟列表是已知坑）、
//       jobs 页表格结构、playbooks 页列表/编辑器入口
const { chromium } = require('@playwright/test');
const { baseUrl, admin } = require('./config');

const out = (t, x) => console.log(`\n===== ${t} =====\n` + String(x).slice(0, 2500));

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

  // 菜单组：找 Ansible 相关文本与子项
  const menuText = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.ant-menu-item, .ant-menu-submenu-title'))
      .map(e => e.innerText.trim()).filter(Boolean));
  out('menu items', JSON.stringify(menuText));

  // ---- exec 页 ----
  await page.goto(baseUrl + '/ansible/exec');
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(1500);
  out('/ansible/exec body', await page.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n')));
  // 主机选择控件结构
  const selects = await page.locator('.ant-select').evaluateAll(els =>
    els.map(e => ({ cls: e.className.slice(0, 80), text: e.innerText.slice(0, 100) })));
  out('ant-select elements', JSON.stringify(selects, null, 1));

  // 点击主机选择器看下拉结构
  const hostSelect = page.locator('.ant-select').first();
  if (await hostSelect.count()) {
    await hostSelect.click();
    await page.waitForTimeout(800);
    const options = await page.locator('.ant-select-dropdown .ant-select-item').evaluateAll(els =>
      els.map(e => ({ text: e.innerText.trim(), cls: e.className.slice(0, 60) })));
    out('dropdown options', JSON.stringify(options, null, 1).slice(0, 1500));
    await page.keyboard.press('Escape');
  }

  // ---- jobs 页 ----
  await page.goto(baseUrl + '/ansible/jobs');
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(1500);
  out('/ansible/jobs body', await page.evaluate(() => {
    const t = document.body.innerText;
    return t.slice(t.indexOf('执行历史') > -1 ? t.indexOf('执行历史') : 0).replace(/\n{2,}/g, '\n');
  }));

  // ---- playbooks 页 ----
  await page.goto(baseUrl + '/ansible/playbooks');
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(1500);
  out('/ansible/playbooks body', await page.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n')));

  await browser.close();
})().catch(e => { console.error('PROBE-ANS FAILED:', e.message); process.exit(1); });
