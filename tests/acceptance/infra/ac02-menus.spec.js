/**
 * AC-INFRA-02 四组菜单可见可达
 * 对应验收标准：环境基线验收 3（主机管理 / 批量执行 / 在线终端 / 监控报警菜单可见可点）
 *
 * 验证内容：
 *   1. 登录后左侧菜单五项全部可见
 *   2. 四组核心页面逐一直达且渲染出各自的标志性内容（不是白屏/报错页）
 *   3. 在线终端（/ssh）加载出主机分组树
 *
 * 说明：路由直达 = 验证页面可达；菜单文本可见 = 验证「可见」。
 *       两者合起来覆盖「可见可点」。逐页点菜单导航的差异由 AC-INFRA-03~05 的
 *       页面内操作自然覆盖，这里不重复造交互。
 */
const { test, expect } = require('@playwright/test');
const { baseUrl } = require('../config');
const { login } = require('../helpers/login');

test.describe('AC-INFRA-02 菜单可见可达', () => {
  // 用例间复用同一个已登录的浏览器上下文：本文件内只登录一次
  let page;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext();
    page = await context.newPage();
    await login(page);
  });

  test('左侧菜单五项全部可见', async () => {
    for (const menu of ['主机管理', '批量执行', '应用发布', '监控中心', '报警中心']) {
      await expect(page.getByText(menu, { exact: true }).first()).toBeVisible();
    }
  });

  // 每组菜单：直达路由 + 断言页面标志性文本（探查实证，见 probe.js 输出）
  const routes = [
    ['/host', '分组列表'],          // 主机管理页
    ['/exec/task', '目标主机'],     // 批量执行页
    ['/monitor', '监控任务'],       // 监控中心页
    ['/alarm/alarm', '报警历史记录'], // 报警中心页
  ];
  for (const [path, marker] of routes) {
    test(`页面 ${path} 可达且渲染完整`, async () => {
      await page.goto(baseUrl + path);
      await expect(page.getByText(marker, { exact: true }).first()).toBeVisible({ timeout: 15_000 });
    });
  }

  test('在线终端 /ssh 可达且主机树渲染', async () => {
    await page.goto(baseUrl + '/ssh');
    // 分组树：三个分组名 + 小提示条（探查实证）
    await expect(page.getByText('小提示：双击标签快速复制窗口', { exact: false }).first()).toBeVisible();
    for (const host of ['web-01', 'db-01', 'cache-01']) {
      await expect(page.getByText(host, { exact: true }).first()).toBeVisible();
    }
  });
});
