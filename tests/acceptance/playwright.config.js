// Playwright 配置 —— OpsForge 验收套件
// 文档：https://playwright.dev/docs/test-configuration
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  // 用例按模块分子目录：infra/ = INFRA 模块（后续模块各建子目录，如 ans/）
  testDir: '.',
  timeout: 60_000,
  // 串行执行：用例共享同一平台与账号（批量执行/发布互相有状态影响），并行会互相干扰
  workers: 1,
  // 教学向：失败立即暴露，不重试掩盖问题；报告里附失败截图
  retries: 0,
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  outputDir: 'test-results',
  use: {
    baseURL: 'http://localhost',
    // 用系统已装的 Chrome，免去下载 Chromium（本机网络环境受限）
    channel: 'chrome',
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    actionTimeout: 15_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
