/**
 * AC-ANS-04 运行中任务终止
 * 对应验收标准：实时回传验收 2（运行中任务可终止，状态即时变更）
 *
 * 验证内容：
 *   1. 对 1 台主机发起长任务（shell sleep 60）
 *   2. 运行中出现「终止」按钮，点击后短时间内状态变为失败且带 killed 标记
 *
 * 只选 1 台：kill 链路的用户可见行为与台数无关，1 台足够且省资源。
 * 已知行为：终止在 UI 上 3 秒内呈现「执行失败 reason: killed」。
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');

test('AC-ANS-04 运行中任务可终止且状态即时变更', async ({ page }) => {
  await login(page);
  await page.goto('http://localhost/ansible/exec');
  await expect(page.getByText('Ansible 执行', { exact: true }).first()).toBeVisible();

  // 1. 只选 web-01 一台
  await page.locator('.ant-select-multiple').first().click();
  await page.locator('.ant-select-dropdown .ant-select-item-option', { hasText: 'web-01' }).first().click();
  await page.keyboard.press('Escape');
  await expect(page.locator('.ant-select-multiple .ant-select-selection-item')).toHaveCount(1);

  // 2. 长任务：shell sleep 60
  await page.locator('input[placeholder="模块名，如 shell"]').fill('shell');
  await page.locator('input[placeholder="模块参数，如 sleep 3 && hostname"]').fill('sleep 60');
  await page.locator('button:has-text("执行")').click();

  // 3. 运行中出现终止按钮并点击（任务先于 sleep 结束前终止）
  const killBtn = page.locator('button:has-text("终止")').first();
  await expect(killBtn).toBeVisible({ timeout: 30_000 });
  await killBtn.click();

  // 4. 状态即时变更：3s 内出现失败 + killed（给 UI 轮询留点到 10s 余量，T0 实测 3s）
  await expect(page.getByText(/执行失败/, { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/killed/, { exact: false }).first()).toBeVisible({ timeout: 5_000 });
});
