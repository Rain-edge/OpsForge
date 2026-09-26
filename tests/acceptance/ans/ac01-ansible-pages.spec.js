/**
 * AC-ANS-01 「Ansible 自动化」菜单组与三页可达
 * 对应验收标准：实时回传约定（前端路由 /ansible/exec、/ansible/jobs + Playbook 管理 playbook 管理页）
 *
 * 验证内容：
 *   1. 登录后左侧菜单出现「Ansible 自动化」（二开新增的菜单组）
 *   2. 执行页 / 历史页 / Playbook 管理页直达且渲染出各自标志性内容
 */
const { test, expect } = require('@playwright/test');
const { baseUrl } = require('../config');
const { login } = require('../helpers/login');

const PAGES = [
  ['/ansible/exec', 'Ansible 执行'],
  // 注意是子串匹配：页面标题为「Ansible 执行历史」整段文本，exact 匹配会落空
  ['/ansible/jobs', '执行历史'],
  ['/ansible/playbooks', 'Playbook 管理'],
];

test.describe('AC-ANS-01 菜单与页面', () => {
  let page;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext();
    page = await context.newPage();
    await login(page);
  });

  test('菜单组「Ansible 自动化」可见', async () => {
    await expect(page.getByText('Ansible 自动化', { exact: true }).first()).toBeVisible();
  });

  for (const [path, marker] of PAGES) {
    test(`页面 ${path} 可达且渲染完整`, async () => {
      await page.goto(baseUrl + path);
      await expect(page.getByText(marker, { exact: false }).first()).toBeVisible({ timeout: 15_000 });
    });
  }
});
