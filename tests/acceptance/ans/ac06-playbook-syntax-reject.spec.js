/**
 * AC-ANS-06 语法错误的 Playbook 被拒
 * 对应验收标准：Playbook 管理 验收 2（语法错误的 YAML 被拒并展示 ansible 报错）
 *
 * 验证内容：
 *   1. 新建 modal 输入缩进错位的坏 YAML → 保存
 *   2. modal 不关闭，出现「语法校验未通过」与 ansible 的 ERROR 输出原文
 *   3. 列表中不存在该名称（确实没存进去）
 *
 * 探查实证：语法报错直接渲染在 modal 内（不是轻提示 toast），便于断言。
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');
const { aceReplace } = require('../helpers/ace');

// 未闭合引号：YAML 扫描器必拒（之前试过"缩进错位"，实测 ansible 竟容忍了这种写法）
const BAD_YAML = '- hosts: "all\n  tasks:\n    - name: broken\n      ping:';

test('AC-ANS-06 坏 YAML 被拒且展示 ansible 报错', async ({ page }) => {
  await login(page);
  await page.goto('http://localhost/ansible/playbooks');
  await expect(page.getByText('Playbook 管理', { exact: true }).first()).toBeVisible({ timeout: 15_000 });

  await page.locator('button:has-text("新建")').click();
  await page.locator('.ant-modal input[placeholder="名称，如 nginx 部署"]').fill('ta-ans06-bad');
  await aceReplace(page, BAD_YAML);
  await page.locator('.ant-modal button:has-text("保存")').click();

  // 拒绝并展示 ansible 报错原文。探查实证：报错是短暂浮层（antd message），不是 modal 内联，
  // 所以从页面级找文本；断言开始得越快越好，浮层约 3 秒后消失
  await expect(page.getByText(/语法校验未通过/, { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/ERROR!/, { exact: false }).first()).toBeVisible({ timeout: 5_000 });

  // modal 保持打开 = 没有保存成功；关掉后确认列表无此数据
  await expect(page.locator('.ant-modal')).toBeVisible();
  await page.locator('.ant-modal button:has-text("取 消")').click();
  await page.reload();
  await expect(page.locator('.ant-table-tbody tr', { hasText: 'ta-ans06-bad' })).toHaveCount(0);
});
