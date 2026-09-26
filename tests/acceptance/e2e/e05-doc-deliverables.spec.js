/**
 * AC-E2E-05 文档与产物在库完整（总验收标准 4/5）
 * 对应模块：文档收尾
 *
 * 验证内容（文件级 + 内容抽验，纯仓库断言，秒级）：
 *   1. 产物链完整：README / LICENSE / RUNBOOK /
 *      experiments 记录 + summary.json / oss-diff 脚本
 *   2. 内容抽验：README 含 mermaid 架构图与二开声明；summary.json 可解析且指标带 source 溯源
 *   3. LICENSE 为 AGPL-3.0
 */
const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..', '..'); // e2e → acceptance → tests → 仓库根
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');
const mustExist = (...p) => expect(fs.existsSync(path.join(ROOT, ...p)), `${path.join(...p)} 应在库`).toBe(true);

test('AC-E2E-05 文档产物链完整且合规', () => {
  // 1. 产物清单（总标准 4）
  mustExist('README.md');
  mustExist('LICENSE');
  mustExist('docs', 'opsforge', 'RUNBOOK.md');
  mustExist('scripts', 'oss-diff.py');
  mustExist('docs', 'opsforge', 'experiments', '2026-10-02-full-replay.md');
  mustExist('docs', 'opsforge', 'experiments', 'summary.json');

  // 2. 内容抽验
  const readme = read('README.md');
  expect(readme).toContain('mermaid');       // 架构图
  expect(readme).toMatch(/Spug/i);           // 二开声明（基于开源 Spug）
  expect(readme).toMatch(/AGPL/i);           // 许可证声明（AGPL）
  const summary = JSON.parse(read('docs', 'opsforge', 'experiments', 'summary.json'));
  const flat = JSON.stringify(summary);
  expect(flat).toContain('source');          // 指标带 source 溯源

  // 3. LICENSE 为 AGPL-3.0
  expect(read('LICENSE')).toMatch(/GNU AFFERO GENERAL PUBLIC LICENSE|AGPL/i);
});
