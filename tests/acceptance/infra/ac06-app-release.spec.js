/**
 * AC-INFRA-06 应用发布端到端
 * 对应验收标准：节点实验环境验收 4（原生「应用发布」做一次演示级发布，留操作记录）
 *
 * 验证内容：
 *   1. 幂等发布脚本 lab/scripts/demo_release.sh 重跑成功（可重复验收的载体）
 *   2. UI 发布申请列表「发布成功」计数 +1（新申请发布成功）
 *   3. 产物落到 web 组两节点（docker exec 检查 /home/ops/demo/index.html 非空）
 *
 * 设计说明：触发发布走脚本而非 UI 点击——脚本是节点实验环境的交付物（幂等设计），
 * UI 上「新建申请→发布」表单流程复杂且脚本已覆盖 API 语义；本用例的关注点是
 * 「发布这件事在平台上发生且用户可见」，脚本触发 + UI 验证结果即可证明。
 */
const { test, expect } = require('@playwright/test');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const path = require('node:path');
const { login } = require('../helpers/login');

const exec = promisify(execFile);
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..'); // infra → acceptance → tests → 仓库根

// 读 UI 上「发布成功(N)」标签里的 N
async function successCount(page) {
  const text = await page.getByText(/发布成功\(\d+\)/).first().innerText();
  return Number(text.match(/发布成功\((\d+)\)/)[1]);
}

test('AC-INFRA-06 应用发布：脚本重跑 → UI 可见新成功申请 → 产物落地 web 组', async ({ page }) => {
  await login(page);
  await page.goto('http://localhost/deploy/request');
  await expect(page.getByText(/发布成功\(\d+\)/).first()).toBeVisible({ timeout: 15_000 });
  const before = await successCount(page);

  // ---- 1. 幂等脚本重跑（会新建一条发布申请并执行发布）----
  // 脚本自身先查后建，重复执行安全
  const { stdout } = await exec('bash', ['lab/scripts/demo_release.sh'], {
    cwd: REPO_ROOT,
    timeout: 120_000,
    encoding: 'utf8',
  });
  console.log('demo_release.sh output:\n' + stdout.split('\n').slice(-10).join('\n'));

  // ---- 2. UI 验证：发布成功计数 +1（刷新页面读取）----
  await page.reload();
  await expect(page.getByText(/发布成功\(\d+\)/).first()).toBeVisible({ timeout: 15_000 });
  const after = await successCount(page);
  expect(after, `发布成功数应从 ${before} 增至 ${before + 1}`).toBe(before + 1);

  // ---- 3. 产物落地：web 组两节点都有非空的 index.html（docker 层断言）----
  for (const node of ['web-01', 'web-02']) {
    const { stdout: out } = await exec(
      'docker',
      ['exec', node, 'sh', '-c', 'test -s /home/ops/demo/index.html && echo PRODUCT_OK'],
      { timeout: 30_000, encoding: 'utf8' }
    );
    expect(out.trim(), `${node} 产物应为非空文件`).toContain('PRODUCT_OK');
  }
});
