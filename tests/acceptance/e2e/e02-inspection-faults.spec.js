/**
 * AC-E2E-02 批量巡检：3 类故障检出 + 报告产出 + 恢复 diff（总验收标准 2）
 * 对应模块：巡检报表（批量巡检报表）
 *
 * 验证内容（故障注入已获用户授权，注入后必须恢复）：
 *   1. 注入 3 类故障（与巡检报表实施过程同款）：
 *      磁盘满（db-01 tmpfs 4m + dd 填满）/ 进程挂（web-01 stop nginx）/ 端口占用（web-02 python3 占 80）
 *   2. UI 点「立即巡检」→ 新轮次检出 5 crit（3 类全中，统计「正常 37」）
 *   3. HTML 报告可下载且含 CRIT 内容（xlsx 同源，由巡检报表验收覆盖下载重开）
 *   4. 全部恢复后再巡检 → 42 ok（diff crit→ok 由 manifest 自动记录）
 *
 * 注入命令与恢复方式来自 docs/opsforge/experiments/2026-10-02-full-replay.md（实施过程实测）。
 * Node 的 child_process 不走 Git Bash，docker exec 路径参数无 MSYS 转换坑。
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');
const { apiLogin, apiGet } = require('../helpers/spug-api');
const { clearInjectedFaults, ensureLabHealthy, dex } = require('../helpers/lab-env');

// 巡检注入 + 两次巡检 + 恢复，全程远超默认 60s，用例级放宽
test.setTimeout(300_000);

const ensureRecovered = clearInjectedFaults;

// 读轮次表首行的轮次 ID（antd 表格渲染完成后取首行首列）
async function topReportId(page) {
  const row = page.locator('.ant-table-tbody tr').first();
  await row.waitFor({ state: 'visible', timeout: 20_000 });
  const idText = await row.locator('td').first().innerText();
  return Number(idText.trim());
}

test('AC-E2E-02 巡检 3 类故障 5 crit 检出 → 恢复 42 ok', async ({ page, request }) => {
  const token = await apiLogin(request);
  await login(page);
  await page.goto('http://localhost/ansible/inspections');
  await expect(page.getByText('批量巡检报表', { exact: true }).first()).toBeVisible({ timeout: 15_000 });

  // 记录当前最新轮次 id（后面断言只看自己触发的轮）
  const beforeId = await topReportId(page);
  expect(beforeId).toBeGreaterThan(0);

  // ---- 1. 注入 3 类故障（回环 tmpfs 不伤容器层）----
  await ensureLabHealthy(); // 先收敛，保证注入前的 42 ok 基线成立
  await dex('db-01', 'mkdir -p /mnt/fault && mount -t tmpfs -o size=4m tmpfs /mnt/fault && dd if=/dev/zero of=/mnt/fault/fill bs=1M count=5 || true');
  await dex('web-01', 'systemctl stop nginx');
  await dex('web-02', 'systemctl stop nginx && (python3 -m http.server 80 >/dev/null 2>&1 &)');

  // ---- 2. UI 立即巡检 → 故障轮 5 crit ----
  await page.locator('button:has-text("立即巡检")').click();
  // 巡检一轮 ~1s/7 节点 + 编排，等新轮出现且统计为「正常 37」
  const faultRow = page.locator('.ant-table-tbody tr').first();
  await expect
    .poll(() => topReportId(page), { timeout: 120_000, intervals: [2000, 5000] })
    .toBeGreaterThan(beforeId);
  await expect(faultRow.getByText(/正常\s*37/)).toBeVisible({ timeout: 30_000 });

  // ---- 3. HTML 报告可取且含 CRIT（与 UI 预览/下载按钮同 URL，x-token 走 query）----
  const faultId = await topReportId(page);
  const htmlResp = await request.get(
    `http://localhost/api/ansible_ops/inspections/${faultId}/files/html/?x-token=${token}`
  );
  expect(htmlResp.status(), 'HTML 报告应可下载').toBe(200);
  const html = await htmlResp.text();
  expect(html).toMatch(/CRIT| crit/i);
  expect(html).toMatch(/db-01|web-01|web-02/); // 涉事节点在报告中

  // ---- 4. 全部恢复 → 再巡检 42 ok ----
  await ensureRecovered();
  await page.locator('button:has-text("立即巡检")').click();
  const okRow = page.locator('.ant-table-tbody tr').first();
  await expect
    .poll(() => topReportId(page), { timeout: 120_000, intervals: [2000, 5000] })
    .toBeGreaterThan(faultId);
  await expect(okRow.getByText(/正常\s*42/)).toBeVisible({ timeout: 60_000 });
});

test.afterAll(async () => {
  // 兜底：无论用例在哪一步失败，环境必须回健康基线
  await ensureRecovered();
});
