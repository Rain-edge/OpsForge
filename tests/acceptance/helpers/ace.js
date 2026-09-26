// ACE 编辑器写入助手（Playbook 名称/内容表单用）
//
// 为什么不能 keyboard.type：ACE 开着 auto-indent，逐键输入的换行会触发自动缩进，
// 与 YAML 里手写的空格叠加导致缩进翻倍、语法校验必挂（实测教训，见 debug-ac05.js）。
// insertText 一次性插入整段文本，不触发逐键缩进，缩进与原文逐字一致（实测验证）。
//
// 注意：新建弹窗的 ACE 自带默认模板，写入前必须先全选删除。

async function aceReplace(page, text) {
  await page.locator('.ant-modal .ace_content').last().click();
  await page.keyboard.press('Control+a');
  await page.keyboard.press('Backspace');
  await page.keyboard.insertText(text);
  // 写完稍微停一拍，等 ACE 内部状态同步
  await page.waitForTimeout(200);
}

module.exports = { aceReplace };
