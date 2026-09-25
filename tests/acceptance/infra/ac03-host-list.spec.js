/**
 * AC-INFRA-03 主机列表与分组（7 台纳管结果）
 * 对应验收标准：节点实验环境验收 2（主机列表 7 台连通性通过，分组 web×2 / db×2 / cache×3）
 *
 * 验证内容：
 *   1. 分组树含 web / db / cache 三组
 *   2. 逐组点击后，表格行数与主机名与约定表一致
 *   3. 每台主机状态为「已验证」（= 平台对它做过 SSH 连通性验证且通过）
 *
 * 已知 UI 行为（探查实证）：主机表格按「当前选中分组」过滤显示，默认显示第一组，
 * 所以必须逐组点击来凑齐 7 台，而不是指望一页显示全部。
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');

// 约定节点表（接口约定）——用例即文档，改节点清单时同步改这里
const GROUPS = {
  cache: ['cache-01', 'cache-02', 'cache-03'],
  db: ['db-01', 'db-02'],
  web: ['web-01', 'web-02'],
};

test.describe('AC-INFRA-03 主机列表与分组', () => {
  let page;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext();
    page = await context.newPage();
    await login(page);
    await page.goto('http://localhost/host');
    await expect(page.getByText('分组列表', { exact: true }).first()).toBeVisible();
  });

  for (const [group, hosts] of Object.entries(GROUPS)) {
    test(`分组 ${group} 含 ${hosts.length} 台已验证主机`, async () => {
      // 点分组树节点（exact 匹配避免选中 cache-01 这类主机名）
      await page.getByText(group, { exact: true }).first().click();
      // 表格按组过滤：断言本组主机全部在列，且状态列是「已验证」
      for (const host of hosts) {
        const row = page.locator('.ant-table-tbody tr', { hasText: host });
        await expect(row).toBeVisible();
        await expect(row.locator('text=已验证').first()).toBeVisible();
      }
      // 表格行数 = 组内主机数（防止多显示/少显示）
      const rowCount = await page.locator('.ant-table-tbody tr:has(td)').count();
      expect(rowCount).toBe(hosts.length);
    });
  }

  test('7 台主机全部为已验证状态', async () => {
    // 逐组统计「已验证」行数之和 = 7
    let verified = 0;
    for (const group of Object.keys(GROUPS)) {
      await page.getByText(group, { exact: true }).first().click();
      verified += await page.locator('.ant-table-tbody tr', { hasText: '已验证' }).count();
    }
    expect(verified).toBe(7);
  });
});
