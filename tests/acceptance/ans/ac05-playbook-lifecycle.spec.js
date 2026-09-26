/**
 * AC-ANS-05 Playbook 版本化全生命周期
 * 对应验收标准：Playbook 管理 验收 1（建 → 改两版 → versions ≥2 → diff 可见 → rollback 内容回退）
 *
 * 验证内容（全 UI 流程 + API 内容对照）：
 *   1. 新建 playbook（V1 YAML）
 *   2. 编辑为 V2 → 版本历史 ≥2 commits
 *   3. 「查看 diff」展示两版差异
 *   4. 「回滚到此版」到 V1 → API 读回 content 已回退（不含 V2 特征串）
 *
 * 探查实证的交互结构（probe-ans-pb*.js + debug-ac05.js）：
 *   - 新建/编辑都是 modal：名称/描述两个 input + ACE 编辑器
 *   - ACE 编辑器新建时带默认模板！必须先全选删除；写入必须用 helpers/ace.js 的
 *     aceReplace（insertText 整段插入）——逐键 type 会触发 ACE auto-indent，
 *     与 YAML 手写空格叠加导致缩进翻倍、语法校验必挂（首跑实测踩中）
 *   - 保存成功 = modal 关闭；列表刷新时序不稳，reload 后再找行
 *   - 版本历史是 ant-modal（标题「{name} · 版本历史（git）」），每行「查看 diff」「回滚到此版」
 *   - 「查看 diff」是 antd 行内展开：展开行 tr.ant-table-expanded-row 插在数据行后，
 *     行定位必须用 tr.ant-table-row 过滤，否则 nth 会错位到展开行（首跑实测踩中）
 *   - 回滚是 Popconfirm 确认，按钮文案「确 定」（antd 带空格，T3 踩坑）
 *   - 版本行最新在上：V2 是第 1 行，V1 是第 2 行
 */
const { test, expect } = require('@playwright/test');
const { login } = require('../helpers/login');
const { apiLogin, apiGet, apiDelete } = require('../helpers/spug-api');
const { aceReplace } = require('../helpers/ace');

const NAME = 'ta-ans05-lifecycle';
const V1 = '- hosts: all\n  tasks:\n    - name: ping all\n      ping:';
const V2 = '- hosts: all\n  tasks:\n    - name: ping all v2\n      ping:';

test.describe('AC-ANS-05 Playbook 生命周期', () => {
  let page;
  let request;
  let playbookId;

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext();
    page = await context.newPage();
    request = context.request;
    await login(page);
  });

  test('新建 V1 → 编辑 V2 → 版本与 diff → 回滚回 V1', async () => {
    // 0. 防御性清理：上次运行残留的同名 playbook（名称 unique，不删会撞名）
    await page.goto('http://localhost/ansible/playbooks');
    await expect(page.getByText('Playbook 管理', { exact: true }).first()).toBeVisible({ timeout: 15_000 });
    const token = await apiLogin(request);
    const stale = page.locator('.ant-table-tbody tr', { hasText: NAME });
    if (await stale.count()) {
      const staleId = Number((await stale.innerText()).trim().split('\t')[0]);
      await apiDelete(request, token, `/api/ansible_ops/playbooks/${staleId}/`);
      await page.reload();
      await expect(page.getByText('Playbook 管理', { exact: true }).first()).toBeVisible();
    }

    // 1. 新建 V1
    await page.locator('button:has-text("新建")').click();
    await page.locator('.ant-modal input[placeholder="名称，如 nginx 部署"]').fill(NAME);
    await page.locator('.ant-modal input[placeholder="描述（可选）"]').fill('验收用例数据');
    await aceReplace(page, V1);
    await page.locator('.ant-modal button:has-text("保存")').click();
    // 保存成功的直接标志 = modal 关闭；列表刷新时序不稳，reload 后再找行
    await expect(page.locator('.ant-modal')).toBeHidden({ timeout: 30_000 });
    await page.reload();
    const row = page.locator('.ant-table-tbody tr', { hasText: NAME });
    await expect(row).toBeVisible({ timeout: 15_000 });
    playbookId = Number((await row.innerText()).trim().split('\t')[0]);

    // 2. 编辑为 V2
    await row.locator('text=编辑').click();
    await page.locator('.ant-modal input[placeholder="名称，如 nginx 部署"]').waitFor();
    await aceReplace(page, V2);
    await page.locator('.ant-modal button:has-text("保存")').click();
    await page.locator('.ant-modal').waitFor({ state: 'hidden', timeout: 30_000 });

    // 3. 版本历史：≥2 commits，最新（V2）在上
    await row.locator('text=版本').click();
    const verModal = page.locator('.ant-modal', { hasText: '版本历史' });
    await expect(verModal).toBeVisible();
    // 只数数据行（tr.ant-table-row）——下一步点「查看 diff」会插入 ant-table-expanded-row
    // 展开行，若不过滤会把 nth(1) 错位到 diff 展开行上（首跑实测踩中）
    const verRows = verModal.locator('.ant-table-tbody tr.ant-table-row');
    await expect(verRows).toHaveCount(2, { timeout: 15_000 });
    await expect(verRows.first()).toContainText('update');

    // 4. 查看最新版 diff：行内展开出 git diff，能看到 V2 特征串
    await verRows.first().locator('text=查看 diff').click();
    await expect(verModal.getByText('ping all v2', { exact: false }).first()).toBeVisible({ timeout: 10_000 });

    // 5. 回滚到 V1（数据行第 2 行）→ Popconfirm「确 定」
    // 回滚后 modal 保持打开（可继续查看历史），回滚产生的新 commit 出现在列表顶部
    await verRows.nth(1).locator('text=回滚到此版').click();
    await page.locator('button:has-text("确 定")').last().click();
    await expect(verRows.first()).toContainText('rollback', { timeout: 15_000 });
    await verRows.first().locator('text=查看 diff').click();
    await expect(verModal.getByText('ping all', { exact: false }).first()).toBeVisible({ timeout: 10_000 });

    // 6. 关弹窗（antd 关闭的 modal 其 DOM 仍留在页面上，Close 按钮定位会命中隐藏元素，
    //    用 Escape 关最稳），API 对照：内容已回退到 V1（含 ping all、不含 V2 特征 v2）
    await page.keyboard.press('Escape');
    await page.locator('.ant-modal', { hasText: '版本历史' }).waitFor({ state: 'hidden', timeout: 15_000 });
    const detail = await apiGet(request, token, `/api/ansible_ops/playbooks/${playbookId}/`);
    expect(detail.content, '回滚后应回 V1').toContain('name: ping all');
    expect(detail.content, '回滚后不应残留 V2 特征').not.toContain('v2');
  });

  test.afterAll(async () => {
    // 清理测试数据，套件可无限次重跑
    if (playbookId) {
      const token = await apiLogin(request);
      await apiDelete(request, token, `/api/ansible_ops/playbooks/${playbookId}/`).catch(() => {});
    }
  });
});
