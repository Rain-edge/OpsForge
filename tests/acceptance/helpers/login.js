// 登录助手：所有用例共用
// 平台登录页有「获取验证码」字段，但密码登录场景非必填，留空即可（探查实证）
// 登录成功后本地环境会弹「安全警告」modal（nginx 未传 X-Forwarded-For，RUNBOOK §4），
// 必须点「知道了」关闭，否则后续断言会被遮罩挡住
const { baseUrl, admin } = require('../config');

async function login(page) {
  await page.goto(baseUrl + '/');
  await page.locator('#username').fill(admin.username);
  await page.locator('#password').fill(admin.password);
  // 按钮文本是「登 录」（中间有空格），来自上游源码，不要"修正"它
  await page.locator('button:has-text("登 录")').click();
  await page
    .locator('.ant-modal button:has-text("知道了")')
    .first()
    .click({ timeout: 10_000 });
  // 等弹窗遮罩消失，避免遮挡后续点击
  await page.locator('.ant-modal-wrap').waitFor({ state: 'hidden', timeout: 10_000 }).catch(() => {});
}

module.exports = { login };
