/**
 * AC-ANS-08 后端测试全量回归（容器内 pytest）
 * 对应验收标准：执行引擎验收 1 的测试部分（pytest ≥8 用例全过；累积 27→42→77）
 *
 * 验证内容：
 *   容器内跑 apps/ansible_ops/tests 全量，exit 0 且 passed ≥77（只增不减）。
 *
 * 为什么在容器内跑（踩坑）：工作区上游基线与镜像内 v4.0.1 不同源，
 * 工作区根本无法运行这套测试；镜像内才是真实运行环境。
 */
const { test, expect } = require('@playwright/test');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const exec = promisify(execFile);

test('AC-ANS-08 容器内 pytest 全量通过（≥77 用例）', async () => {
  const { stdout } = await exec(
    'docker',
    ['exec', 'spug', 'sh', '-c', 'cd /data/spug/spug_api && python -m pytest apps/ansible_ops/tests -q'],
    { timeout: 300_000, encoding: 'utf8' }
  );

  // exit 0 已经由 execFile 保证（非零即抛错）；这里再核对通过数下限
  const m = stdout.match(/(\d+) passed/);
  expect(m, 'pytest 输出应含 "N passed"').toBeTruthy();
  expect(Number(m[1]), '通过用例数不应低于 Playbook 管理 交付时的 77').toBeGreaterThanOrEqual(77);
  console.log(`pytest: ${m[0]}（输出尾部: ${stdout.trim().split('\n').slice(-2).join(' | ')}）`);
});
