/**
 * AC-ANS-02 ad-hoc 执行 7 台 raw ping 成功（执行引擎 + 实时输出的用户可见面）
 * 对应验收标准：执行引擎验收 2（raw ping 7/7 ok）+ 实时回传验收 1 的 UI 呈现部分
 *
 * 验证内容：
 *   1. exec 页主机多选框选中全部 7 台（选择框出现 7 个标签）
 *   2. 填模块名 ping → 点「执行」
 *   3. 输出区出现 ansible 逐主机输出（SUCCESS 行）与终态摘要（ok 7）
 *
 * 交互探查实证（probe-ans.js / probe-ans-exec.js）：
 *   - 主机多选是 antd Select multiple，下拉 7 个 option 全部真实渲染（无虚拟列表裁剪，
 *     T2 踩坑里的虚拟列表问题不在此页），逐个 click 即可
 *   - 模块名/参数是两个普通 input（placeholder「模块名，如 shell」/「模块参数，如 …」）
 *   - ping 模块幂等语义：7 台全部 ok、changed=0
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');

const ALL_HOSTS = ['web-01', 'web-02', 'db-01', 'db-02', 'cache-01', 'cache-02', 'cache-03'];

test('AC-ANS-02 ad-hoc raw ping 7/7 ok 且输出实时呈现', async ({ page }) => {
  await login(page);
  await page.goto('http://localhost/ansible/exec');
  await expect(page.getByText('Ansible 执行', { exact: true }).first()).toBeVisible();

  // 1. 主机多选：点开下拉 → 逐个点 option → 收起
  await page.locator('.ant-select-multiple').first().click();
  for (const host of ALL_HOSTS) {
    // option 文本形如 "web-01 (web-01)"，用子串匹配主机名
    await page.locator('.ant-select-dropdown .ant-select-item-option', { hasText: host }).first().click();
  }
  await page.keyboard.press('Escape');
  // 选中后选择框内出现 7 个标签
  await expect(page.locator('.ant-select-multiple .ant-select-selection-item')).toHaveCount(7);

  // 2. ad-hoc 模块表单（默认 radio 已在 ad-hoc 档）
  // 坑：模块名/参数框出厂预填了示例值（"shell" / "hostname"），只覆盖模块名的话
  // 参数框残留的 hostname 会让 ping 报 "has extra params"（ansible 退出码 4）——
  // 用例首跑实测踩中，两个框都要显式写
  await page.locator('input[placeholder="模块名，如 shell"]').fill('ping');
  await page.locator('input[placeholder="模块参数，如 sleep 3 && hostname"]').fill('');

  // 3. 执行并等待终态：输出区从占位文本变为真实 ansible 输出
  await page.locator('button:has-text("执行")').click();
  // 逐主机 SUCCESS 行（ansible 输出格式 `host | SUCCESS => {...}`）
  await expect(page.getByText('| SUCCESS', { exact: false }).first()).toBeVisible({ timeout: 30_000 });
  // 7 台都要出现
  for (const host of ALL_HOSTS) {
    await expect(page.getByText(`${host} | SUCCESS`, { exact: false })).toHaveCount(1);
  }
  // 终态摘要标签（实施过程实测文案「执行成功 ok:…/changed:…」样式）
  await expect(page.getByText(/执行成功/, { exact: false }).first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/ok[::\s]*7/, { exact: false }).first()).toBeVisible();
});
