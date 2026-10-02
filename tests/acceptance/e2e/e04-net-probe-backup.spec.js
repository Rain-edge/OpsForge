/**
 * AC-E2E-04 网络探测矩阵断链检出 + 设备配置备份 diff（验收 1-3 的用户可见面）
 * 对应模块：网络模块（网络探测与设备备份）
 *
 * 验证内容（注入已授权，验完恢复）：
 *   1. 矩阵页基线：9 目标 20 检查项全绿（通 20 / 断 0）
 *   2. 停 cache-03 → 手动探测矩阵出红格（icmp + tcp:22，失败原因 Docker DNS 记录消失）
 *      → 断链告警入报警历史（状态机连续 3 次失败阈值，≤120s）
 *   3. 恢复 cache-03 → 手动探测回绿（通 20 / 断 0）
 *   4. r1 FRR 运行态加路由 → 下一备份轮（120s 间隔）diff 检出，变更表新增记录
 *      → docker restart r1 回滚运行态配置（种子配置只读挂载，重启即复位，实验窗口口径）
 *
 * 时序口径（experiments 实测）：断链检出延迟 = 阈值 3 × 30s 间隔 ≈ 69s；恢复检出 5s；
 * 备份检出延迟 ≤120s。
 */
const { test, expect } = require('@playwright/test');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { login } = require('../helpers/login');
const { apiLogin, apiGet } = require('../helpers/spug-api');
const { ensureLabHealthy } = require('../helpers/lab-env');

const exec = promisify(execFile);

// 矩阵多轮探测 + 断链告警等待 + 备份轮（120s），各步最坏情况相加可到 ~10 分钟
test.setTimeout(900_000);

test('AC-E2E-04 断链矩阵红 → 告警 → 恢复回绿 → 备份 diff 检出', async ({ page, request }) => {
  const token = await apiLogin(request);
  await login(page);
  await page.goto('http://localhost/ansible/net-probe');
  await expect(page.getByText('网络探测矩阵', { exact: true }).first()).toBeVisible({ timeout: 15_000 });

  // 1. 基线全绿。先收敛环境；若前序用例 restart 过 r1/cache-03，探测恢复需几轮，
  //    反复「立即探测 → reload」直到 20/0（240s 上限）
  await ensureLabHealthy();
  await expect.poll(async () => {
    await page.locator('button:has-text("立即探测")').click();
    await page.waitForTimeout(3000);
    await page.reload();
    await expect(page.getByText('网络探测矩阵', { exact: true }).first())
      .toBeVisible({ timeout: 15_000 }).catch(() => {});
    return page.getByText(/通 20 \/ 断 0/).count();
  }, { timeout: 240_000, intervals: [10_000, 20_000] }).toBeGreaterThan(0);

  // 2. 停 cache-03 → 矩阵红（立即探测按钮手动触发一轮，30s 定时轮共用状态机）
  await exec('docker', ['stop', 'cache-03'], { timeout: 60_000 });
  try {
    // 手动探测 3 轮（每轮间隔 ≥3s，让状态机累积连续失败计数）
    for (let i = 0; i < 3; i++) {
      await page.locator('button:has-text("立即探测")').click();
      await page.waitForTimeout(8000);
      await page.reload();
      await expect(page.getByText('网络探测矩阵', { exact: true }).first()).toBeVisible();
      if (await page.getByText(/断 [1-9]/).count()) break;
    }
    await expect(page.getByText(/断 [1-9]/).first()).toBeVisible({ timeout: 30_000 });
    // 红格对应 cache-03 的两个检查项
    const row = page.locator('tr', { hasText: 'cache-03' }).first();
    await expect(row.getByText('断').first()).toBeVisible();

    // 3. 断链告警入报警历史（状态机连续 3 次失败 → Alarm，兜底等定时轮 ≤150s）
    //    坑：告警产生晚于页面加载，DOM 是静态的——poll 里必须反复 reload 才能等到
    await page.goto('http://localhost/alarm/alarm');
    await expect(page.getByText('报警历史记录', { exact: false }).first()).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(async () => {
        await page.reload();
        await expect(page.getByText('报警历史记录', { exact: false }).first())
          .toBeVisible({ timeout: 15_000 }).catch(() => {});
        return page.locator('.ant-table-tbody').getByText(/cache-03|网络断链/, { exact: false }).count();
      }, { timeout: 150_000, intervals: [10_000, 15_000] })
      .toBeGreaterThan(0);
  } finally {
    // 无论断言成败，节点必须拉回来
    await exec('docker', ['start', 'cache-03'], { timeout: 60_000 }).catch(() => {});
  }

  // 4. 恢复回绿（节点刚重建时其服务就绪有快慢——轮询重试，最多 ~150s，与第 1 步同款防竞态）
  await page.goto('http://localhost/ansible/net-probe');
  await expect.poll(async () => {
    await page.locator('button:has-text("立即探测")').click();
    await page.waitForTimeout(8000);
    await page.reload();
    await expect(page.getByText('网络探测矩阵', { exact: true }).first())
      .toBeVisible({ timeout: 15_000 }).catch(() => {});
    return page.getByText(/通 20 \/ 断 0/).count();
  }, { timeout: 150_000, intervals: [10_000, 20_000] }).toBeGreaterThan(0);

  // 5. 备份 diff：r1 运行态加一条静态路由 → 等下一备份轮（120s）→ 变更表新增
  await page.goto('http://localhost/ansible/net-probe');
  const changeCount = () => page.evaluate(() =>
    Number(document.body.innerText.match(/共 (\d+) 条变更/)?.[1] || 0));
  const beforeCount = await changeCount();
  await exec('docker', ['exec', 'r1', 'vtysh', '-c', 'conf t', '-c', 'ip route 10.99.99.0/24 Null0', '-c', 'end'], { timeout: 30_000 });
  try {
    // 备份轮（120s 间隔）检出后落库；页面静态，poll 里必须 reload 才能看到新计数
    await expect
      .poll(async () => {
        await page.reload();
        await expect(page.getByText('网络探测矩阵', { exact: true }).first())
          .toBeVisible({ timeout: 15_000 }).catch(() => {});
        return changeCount();
      }, { timeout: 200_000, intervals: [15_000, 20_000] })
      .toBeGreaterThan(beforeCount);
    await expect(page.getByText('查看 diff', { exact: false }).first()).toBeVisible();
  } finally {
    // 运行态配置重启即回滚（种子只读挂载）；重启引发的短暂探测空窗不足以触发断链告警
    await exec('docker', ['restart', 'r1'], { timeout: 60_000 }).catch(() => {});
  }
});
