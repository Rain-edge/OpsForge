/**
 * AC-INFRA-07 资源预算
 * 对应验收标准：节点实验环境验收 5（平台 + 节点总内存 ≤4.5GB）
 *
 * 背景：宿主 WSL2 限 6GB（~/.wslconfig），OpsForge 平台 + 7 节点总预算 ≤4.5GB，
 * 且须与 CloudForge 的 k3d 集群错峰运行。这条用例守护的是"环境没悄悄超支"。
 *
 * 做法：docker stats --no-stream 读取 OpsForge 全部 9 个容器的实际内存占用求和。
 * 注意读的是 MemUsage 的"已用"值（斜杠前），不是限额值。
 */
const { test, expect } = require('@playwright/test');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const exec = promisify(execFile);

// OpsForge 全部容器白名单（平台 2 + 节点 7）；环境里如有其他项目容器不计入
const CONTAINERS = ['spug', 'spug-db', 'web-01', 'web-02', 'db-01', 'db-02', 'cache-01', 'cache-02', 'cache-03'];
const BUDGET_MIB = 4.5 * 1024; // 4608 MiB

// "549.2MiB / 6GB" → 549.2（统一换算成 MiB）
function toMiB(usage) {
  const m = usage.trim().match(/^([\d.]+)\s*(KiB|MiB|GiB)/);
  if (!m) return 0;
  const v = parseFloat(m[1]);
  if (m[2] === 'GiB') return v * 1024;
  if (m[2] === 'KiB') return v / 1024;
  return v;
}

test('AC-INFRA-07 平台+节点总内存 ≤4.5GB（内存预算）', async () => {
  const { stdout } = await exec(
    'docker',
    ['stats', '--no-stream', '--format', '{{.Name}}\t{{.MemUsage}}', ...CONTAINERS],
    { timeout: 60_000, encoding: 'utf8' }
  );

  const rows = stdout.trim().split('\n').map(line => {
    const [name, usage] = line.split('\t');
    return { name, usedMiB: toMiB(usage) };
  });
  expect(rows).toHaveLength(CONTAINERS.length); // 9 个容器都要取到

  const total = rows.reduce((s, r) => s + r.usedMiB, 0);
  const detail = rows.map(r => `${r.name}=${r.usedMiB.toFixed(1)}MiB`).join(' + ');
  console.log(`内存明细: ${detail}\n总计: ${total.toFixed(1)}MiB / 预算 ${BUDGET_MIB}MiB`);
  expect(total, `9 容器内存合计 ${total.toFixed(1)}MiB 应 ≤ ${BUDGET_MIB}MiB`).toBeLessThanOrEqual(BUDGET_MIB);
});
