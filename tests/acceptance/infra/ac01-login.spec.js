/**
 * AC-INFRA-01 平台可访问与登录
 * 对应验收标准：环境基线验收 2（浏览器打开 localhost 登录成功）
 *
 * 验证内容：
 *   1. 登录页正常渲染（账户/密码输入框出现）
 *   2. admin/spug.cc 登录成功
 *   3. 本地环境特有的「安全警告」弹窗出现并可关闭（点「知道了」）
 *   4. 登录后进入工作台
 *
 * 前置：平台容器已启动且完成初始化
 *   docker compose -f docs/docker/docker-compose.yml -f docker-compose.override.yml up -d
 */
const { test, expect } = require('@playwright/test');
const { baseUrl, admin } = require('../config');

test('AC-INFRA-01 平台登录：打开首页 → 输入管理员凭据 → 关闭安全警告 → 进入工作台', async ({ page }) => {
  // 1. 登录页渲染：两个核心输入框出现
  await page.goto(baseUrl + '/');
  await expect(page.locator('#username')).toBeVisible();
  await expect(page.locator('#password')).toBeVisible();

  // 2. 登录（验证码字段非必填，留空——密码登录场景平台不校验，探查实证）
  await page.locator('#username').fill(admin.username);
  await page.locator('#password').fill(admin.password);
  await page.locator('button:has-text("登 录")').click();

  // 3. 安全警告弹窗出现 = 登录请求已被后端接受（未登录不会弹）
  //    弹窗成因：本地 nginx 未传 X-Forwarded-For，Spug 提示无法按来源 IP 做防护，本地实验可忽略
  const knowBtn = page.locator('.ant-modal button:has-text("知道了")').first();
  await expect(knowBtn).toBeVisible();
  await knowBtn.click();

  // 4. 进入工作台：左侧菜单与「工作台」标题可见
  await expect(page.getByText('工作台', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('主机管理', { exact: true }).first()).toBeVisible();
});
