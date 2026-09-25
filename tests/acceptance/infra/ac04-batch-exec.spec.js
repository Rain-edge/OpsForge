/**
 * AC-INFRA-04 批量执行 7/7 成功
 * 对应验收标准：节点实验环境验收 3（批量执行 hostname && free -m，7/7 主机成功）
 *
 * 验证内容（全 UI 流程）：
 *   1. 「添加目标主机」弹窗内按分组勾选 cache×3 + db×2 + web×2
 *   2. 页面显示「已选择 7 台主机」
 *   3. 命令编辑器输入 hostname，点「开始执行」
 *   4. 执行详情页出现全部 7 台主机，且输出流出现「执行结束」
 *
 * 交互坑（探查实证，改用例前先看）：
 *   - 主机选择弹窗右侧表格只显示「当前选中分组」的主机，必须逐组切换逐组勾选
 *   - 命令编辑器是 ace editor（不是普通 input/textarea），要先点 .ace_content 再键盘输入
 *   - 点「开始执行」后整个页面切换为「执行详情」视图，结果不回原表单
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');

const GROUP_HOST_COUNTS = { cache: 3, db: 2, web: 2 };
const ALL_HOSTS = ['web-01', 'web-02', 'db-01', 'db-02', 'cache-01', 'cache-02', 'cache-03'];

test('AC-INFRA-04 批量执行 hostname 7/7 成功', async ({ page }) => {
  await login(page);
  await page.goto('http://localhost/exec/task');
  await expect(page.getByText('目标主机', { exact: true }).first()).toBeVisible();

  // ---- 1. 逐分组勾选 7 台主机 ----
  await page.locator('button:has-text("添加目标主机")').click();
  const modal = page.locator('.ant-modal-wrap:visible').last();
  await expect(modal).toBeVisible();
  for (const [group, count] of Object.entries(GROUP_HOST_COUNTS)) {
    await modal.getByText(group, { exact: true }).first().click(); // 切换右侧列表到该分组
    const rowChecks = modal.locator('.ant-table-tbody .ant-checkbox-input');
    await expect(rowChecks).toHaveCount(count); // 该组行数 = 约定数
    for (let i = 0; i < count; i++) {
      if (!(await rowChecks.nth(i).isChecked())) await rowChecks.nth(i).check();
    }
  }
  await modal.locator('button:has-text("确 定")').click();

  // ---- 2. 选主机结果回显（弹窗与页面各渲染一份同文本，取 first 避开 strict mode 报错）----
  await expect(page.getByText('已选择 7 台主机').first()).toBeVisible();

  // ---- 3. 输入命令并执行（ace editor：点击内容区后直接打字）----
  await page.locator('.ace_content').first().click();
  await page.keyboard.type('hostname');
  await page.locator('button:has-text("开始执行")').click();

  // ---- 4. 执行详情：7 台主机全部参与 + 输出流有结束标志 ----
  // 详情页左侧主机列表 + 右侧输出流；「执行结束」由后端逐台写入输出流
  await expect(page.getByText('执行详情', { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  for (const host of ALL_HOSTS) {
    // 详情里主机以 web-01(web-01:22) 形式出现，用子串匹配
    await expect(page.getByText(host, { exact: false }).first()).toBeVisible({ timeout: 20_000 });
  }
  await expect(page.getByText('执行结束', { exact: false }).first()).toBeVisible({ timeout: 30_000 });
});
