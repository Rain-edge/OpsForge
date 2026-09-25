/**
 * AC-INFRA-05 Web 终端可用
 * 对应验收标准：节点实验环境验收 3 后半（Web 终端任开一台可用）
 *
 * 验证内容：
 *   1. /ssh 页双击主机树叶子 web-01 打开会话
 *   2. 出现 shell 提示符 ops@web-01
 *   3. 键入命令有回显（真实 SSH 会话在工作）
 *
 * 交互坑（踩坑清单实证）：
 *   - 开会话靠「双击」叶子节点（单击无反应，源码用 nativeEvent.detail > 1 判断）
 *   - xterm 终端元素对常规 Playwright locator 容易超时，验证一律用 evaluate
 *     直查 DOM（.xterm-rows 的 innerText），不走 locator 断言
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');

// 本用例紧跟批量执行（AC-INFRA-04，同时对 7 台建 SSH 通道）之后跑时，
// 会话 WebSocket 偶发被瞬时挤掉（"Connection is closed"）。单独重跑即恢复，
// 属环境抖动而非产品 bug：允许重试一次；若真有问题重试后仍挂，报告照样暴露。
test.describe.configure({ retries: 1 });

// 读取页面上所有 xterm 终端的可见文本（可能同时存在欢迎屏 + 新会话，取全部拼接）
async function terminalText(page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll('.xterm-rows'))
      .map(el => el.innerText)
      .join('\n')
  );
}

test('AC-INFRA-05 Web 终端：双击 web-01 开会话并执行命令', async ({ page }) => {
  await login(page);
  await page.goto('http://localhost/ssh');
  await expect(page.getByText('小提示：双击标签快速复制窗口', { exact: false }).first()).toBeVisible();

  // 前一条用例（AC-INFRA-04）刚建立过 7 条 SSH 通道，节点 sshd 在旧连接未完全回收时
  // 会丢弃新连接（MaxStartups 行为），表现为 WS 首连 "Connection is closed"。
  // 这里等几秒让连接池回收，与文件头的 retries:1 互为双保险。
  await page.waitForTimeout(5000);

  // 1. 双击 web-01 开会话（单击不开）
  await page.getByText('web-01', { exact: true }).first().dblclick();

  // 2. 提示符出现 = SSH 会话建立成功（直查 DOM，不走 locator）
  await expect
    .poll(async () => terminalText(page), { timeout: 20_000, intervals: [1000, 2000, 3000] })
    .toContain('ops@web-01');

  // 3. 命令回显：键入 hostname 回车，输出应包含主机名 web-01
  await page.keyboard.type('hostname\r');
  await expect
    .poll(async () => terminalText(page), { timeout: 15_000, intervals: [1000, 2000] })
    .toMatch(/web-01[\s\S]*web-01/); // 命令回显 + 执行输出都含 web-01
});
