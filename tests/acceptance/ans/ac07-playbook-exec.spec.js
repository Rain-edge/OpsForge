/**
 * AC-ANS-07 Playbook 经 exec 联动执行
 * 对应验收标准：Playbook 管理 验收 3（用该 playbook 经执行引擎 exec 成功执行，联动闭环）
 *
 * 验证内容：
 *   1. Playbook 管理页 smoke-ping 行点「执行」→ 跳转 exec 页并带入 ?playbook=
 *   2. 选择 web 组两台主机（与 Playbook 管理 实测口径一致）
 *   3. 终态「执行成功」，recap ok=4（2 台 × [Gathering Facts + ping]）
 *
 * smoke-ping 是 Playbook 管理 交付的联动验收 playbook（内容 = 编辑器默认模板，hosts: all，
 * 实际命中的是 exec 页所选目标主机），此处只读它，不修改。
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');

test('AC-ANS-07 smoke-ping 从管理页直达执行并成功', async ({ page }) => {
  await login(page);
  await page.goto('http://localhost/ansible/playbooks');
  await expect(page.getByText('Playbook 管理', { exact: true }).first()).toBeVisible({ timeout: 15_000 });

  // 1. 管理页「执行」按钮 → 带 playbook 参数跳转 exec 页
  await page.locator('.ant-table-tbody tr', { hasText: 'smoke-ping' }).locator('text=执行').click();
  await page.waitForURL(/\/ansible\/exec.*playbook=/, { timeout: 15_000 });
  await expect(page.getByText('Ansible 执行', { exact: true }).first()).toBeVisible();

  // 2. exec 页已自动切到 Playbook 档并选中 smoke-ping
  await expect(page.locator('.ant-select', { hasText: 'smoke-ping' }).first()).toBeVisible({ timeout: 10_000 });

  // 3. 选 web 组两台
  await page.locator('.ant-select-multiple').first().click();
  for (const host of ['web-01', 'web-02']) {
    await page.locator('.ant-select-dropdown .ant-select-item-option', { hasText: host }).first().click();
  }
  await page.keyboard.press('Escape');
  await expect(page.locator('.ant-select-multiple .ant-select-selection-item')).toHaveCount(2);

  // 4. 执行 → 终态成功，ok=4（每台 2 个 task：Gathering Facts + ping）
  await page.locator('button:has-text("执行")').click();
  await expect(page.getByText(/执行成功/, { exact: false }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/ok[::\s]*4/, { exact: false }).first()).toBeVisible();
});
