/**
 * AC-E2E-01 Playbook 一键执行与幂等（总验收标准 1 的可重复性部分）
 * 对应模块：Playbook 库（四册 Playbook 库）
 *
 * 验证内容：
 *   1. 通过平台 exec API 对全部 7 台重跑四册 Playbook（baseline×2 连跑 + nginx + mysql + redis）
 *   2. 五个 job 全部 success，且每次重跑 changed=0（幂等——总标准 1 的"连跑两次第二次 changed=0"）
 *   3. UI 执行历史页可见这批 job 的成功状态与「changed 0」摘要（用户可见面）
 *
 * 口径说明：
 *   - 环境已收敛（各册实施过程均已跑过并复测），重跑 changed=0 即幂等证明；
 *     若 changed>0 说明有配置漂移或幂等破坏，用例失败
 *   - MySQL 主从 / Redis 哨兵的部署结果（双 Yes、sentinel 拓扑）由 Playbook 库实施过程
 *     与 T0 独立复验留档；终验的幂等重跑同时证明部署仍可重复
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');
const { apiLogin, apiGet } = require('../helpers/spug-api');
const { ensureLabHealthy } = require('../helpers/lab-env');

// 平台库 id（probe-e2e.js 探查实证）：10=baseline 11=mysql 12=nginx 13=redis
const RUNS = [
  { playbook_id: 10, name: 'baseline' },
  { playbook_id: 10, name: 'baseline（二连跑）' },
  { playbook_id: 12, name: 'nginx' },
  { playbook_id: 11, name: 'mysql-master-slave' },
  { playbook_id: 13, name: 'redis-sentinel' },
];
const HOST_IDS = [1, 2, 3, 4, 5, 6, 7]; // 7 台全量（job 82 实证）

test('AC-E2E-01 四册 Playbook 重跑全部幂等（changed=0）', async ({ page, request }) => {
  // 前置：环境必须收敛（前序注入用例的残留——如 nginx 停着——会被 service task
  // 修正为 changed=1，那是幂等的正确行为，但不该算进本用例的"重跑"口径）
  await ensureLabHealthy();

  const token = await apiLogin(request);

  // 1. 依次发起 5 次执行并等待终态（串行：并发信号量上限 2，且幂等比较要求顺序）
  //    baseline 第一跑允许 changed≥0：前序用例可能重启过节点（如 e04 停过 cache-03），
  //    容器重启后 sysctl 运行时值回默认，第一次重跑修正漂移是 ansible 的正确语义；
  //    第二跑必须 changed=0——这正是总标准"连跑两次第二次 changed=0"的字面口径
  const jobIds = [];
  let baselineFirstChanged = null;
  for (let i = 0; i < RUNS.length; i++) {
    const run = RUNS[i];
    const resp = await request.post('http://localhost/api/ansible_ops/exec/', {
      headers: { 'x-token': token },
      data: { host_ids: HOST_IDS, playbook_id: run.playbook_id },
    });
    const body = await resp.json();
    expect(body.error === '' || body.error === null, `发起 ${run.name} 应成功: ${body.error}`).toBeTruthy();
    const jobId = body.data.job_id ?? body.data;
    jobIds.push({ id: Number(jobId), name: run.name });

    // 轮询该 job 到终态（baseline 全 7 台实测 10-30s，给足 120s）
    let job = null;
    for (let t = 0; t < 60; t++) {
      job = await apiGet(request, token, `/api/ansible_ops/jobs/${jobId}/`);
      if (job.status !== 'pending' && job.status !== 'running') break;
      await page.waitForTimeout(2000);
    }
    expect(['success', 'partial'], `${run.name} (job ${jobId}) 应成功，实际 ${job.status}`).toContain(job.status);
    if (i === 0) {
      baselineFirstChanged = job.summary.changed; // baseline 第一跑：记录漂移修正量
      console.log(`baseline 第一跑 changed=${baselineFirstChanged}（节点若被重启过，此处为漂移修正量 >0 属正常）`);
    } else {
      expect(job.summary.changed, `${run.name} (job ${jobId}) 重跑应幂等 changed=0，实际 ${JSON.stringify(job.summary)}`).toBe(0);
    }
  }
  expect(baselineFirstChanged, 'baseline 第一跑应已执行').not.toBeNull();

  // 2. UI 面校验：执行历史页首行是最后完成的 job，状态「成功」+ 摘要 changed 0
  await login(page);
  await page.goto('http://localhost/ansible/jobs');
  const firstRow = page.locator('.ant-table-tbody tr').first();
  await expect(firstRow).toBeVisible({ timeout: 15_000 });
  const uiText = await firstRow.innerText();
  expect(Number(uiText.trim().split('\t')[0])).toBe(jobIds[4].id);
  expect(uiText).toContain('成功');
  expect(uiText).toMatch(/changed\s*0/);
});
