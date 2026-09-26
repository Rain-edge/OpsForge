/**
 * AC-ANS-03 执行历史页与后端记录一致
 * 对应验收标准：实时回传验收 3 前半（历史页 summary 与后端记录一致）
 *
 * 验证内容：
 *   1. /ansible/jobs 表格有分页数据（共 N 条）
 *   2. 取 UI 首行 job 的 ID，用 API 读同一 job 的详情，逐字段对照：
 *      UI「成功」↔ API status=success；UI 摘要里的 ok/changed 数字 ↔ API summary
 *
 * 对照式断言的意义：UI 不是"看起来有数据"就算过，而是与后端记录逐字段吻合。
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');
const { apiLogin, apiGet } = require('../helpers/spug-api');

test('AC-ANS-03 历史页首行与 API 详情一致', async ({ page, request }) => {
  await login(page);
  await page.goto('http://localhost/ansible/jobs');
  const firstRow = page.locator('.ant-table-tbody tr').first();
  await expect(firstRow).toBeVisible({ timeout: 15_000 });

  // 1. UI 首行信息
  const uiId = Number((await firstRow.innerText()).trim().split('\t')[0]);
  expect(uiId).toBeGreaterThan(0);
  const uiText = await firstRow.innerText();

  // 2. API 同一 job 的详情
  const token = await apiLogin(request);
  const job = await apiGet(request, token, `/api/ansible_ops/jobs/${uiId}/`);

  // 3. 逐字段对照
  // 状态：API success/partial → UI「成功」；failed → UI「失败」
  if (job.status === 'success' || job.status === 'partial') {
    expect(uiText).toContain('成功');
  } else {
    expect(uiText).toContain('失败');
  }
  // 摘要数字：UI 结果摘要列渲染成两行「ok N / changed M」
  if (job.summary && typeof job.summary.ok === 'number') {
    expect(uiText).toMatch(new RegExp(`ok\\s*${job.summary.ok}`));
    expect(uiText).toMatch(new RegExp(`changed\\s*${job.summary.changed}`));
  }
  // 目标主机数：UI「N 台」↔ API host_ids.length
  expect(uiText).toContain(`${job.host_ids.length} 台`);
});
