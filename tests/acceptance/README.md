# OpsForge 验收测试套件（tests/acceptance/）

用真实浏览器验证平台的「用户可见行为」，套件随项目累积：每个模块验收通过后，其用例永久留在仓库里，之后任何改动跑一遍即回归。

技术栈：**Playwright（@playwright/test）+ 系统已装的 Chrome**（免下载 Chromium）。

## 怎么跑

```bash
# 0) 前置：平台与节点容器已启动（见 docs/opsforge/RUNBOOK.md §2 §8.2），本机装了 Chrome

# 1) 首次：安装依赖（本机网络用国内镜像）
cd tests/acceptance
npm install --registry=https://registry.npmmirror.com

# 2) 跑全部用例
npm test

# 3) 只跑某模块（用例按模块分子目录：infra/ ans/ ...）
npm run test:infra

# 4) 跑单条文件
npx playwright test infra/ac01-login.spec.js

# 5) 看可视化报告（含失败截图与操作轨迹）
npm run report
```

## 怎么读懂输出

- 终端逐条打印 `ok N 用例名 (耗时)`；`flaky` = 首次失败、重试后通过（报告里如实标注，不会静默吞掉）
- 失败时自动留存三样东西（都在 `test-results/`）：
  - `test-failed-*.png` 失败瞬间截图
  - `error-context.md` 页面快照 + 定位器分析
  - `trace.zip` 全程操作轨迹（`npx playwright show-trace <路径>` 回放）
- 汇总行：`N passed`（全过）/ `N failed`（有失败，先看 error-context.md）

## 用例清单（INFRA 模块）

| 文件 | 用例 | 对应验收标准 |
|---|---|---|
| `infra/ac01-login.spec.js` | 平台登录 + 关安全警告弹窗 | 环境基线验收 2 |
| `infra/ac02-menus.spec.js` | 四组菜单可见可达 | 环境基线验收 3 |
| `infra/ac03-host-list.spec.js` | 7 台主机分组与「已验证」状态 | 节点实验环境验收 2 |
| `infra/ac04-batch-exec.spec.js` | 批量执行 7/7 成功 | 节点实验环境验收 3 |
| `infra/ac05-web-terminal.spec.js` | Web 终端开会话 + 命令回显 | 节点实验环境验收 3 |
| `infra/ac06-app-release.spec.js` | 应用发布端到端（脚本触发 + UI 验证 + 产物落地） | 节点实验环境验收 4 |
| `infra/ac07-resource-budget.spec.js` | 9 容器总内存 ≤4.5GB | 节点实验环境验收 5（内存预算） |

### 用例清单（ANS 模块）

| 文件 | 用例 | 对应验收标准 |
|---|---|---|
| `ans/ac01-ansible-pages.spec.js` | 「Ansible 自动化」菜单组 + 三页可达 | T2 约定（前端路由） |
| `ans/ac02-adhoc-exec.spec.js` | ad-hoc raw ping 7/7 ok + 实时输出 | T1 验收 2 + T2 验收 1 |
| `ans/ac03-jobs-api-consistency.spec.js` | 执行历史页与 API 详情逐字段一致 | T2 验收 3 |
| `ans/ac04-kill.spec.js` | 运行中任务终止 → killed 即时呈现 | T2 验收 2 |
| `ans/ac05-playbook-lifecycle.spec.js` | 建→改→diff→rollback 全生命周期（API 对照内容） | T3 验收 1 |
| `ans/ac06-playbook-syntax-reject.spec.js` | 坏 YAML 被拒 + ansible 报错展示 | T3 验收 2 |
| `ans/ac07-playbook-exec.spec.js` | smoke-ping 管理页直达执行 ok=4 | T3 验收 3 |
| `ans/ac08-pytest-regression.spec.js` | 容器内 pytest 全量 ≥77 通过 | T1 验收 1 |

### 用例清单（终验 e2e，对照 总验收标准 1-5）

| 文件 | 用例 | 对应总标准 |
|---|---|---|
| `e2e/e01-playbook-idempotent.spec.js` | 四册 Playbook 重跑全 success，baseline 二连跑第二次 changed=0 | 标准 1（幂等） |
| `e2e/e02-inspection-faults.spec.js` | 3 类故障注入 → 巡检 5 crit → HTML 报告 → 恢复 42 ok | 标准 2 |
| `e2e/e03-self-healing.spec.js` | 停 nginx → 告警 → 自动自愈恢复（180s 窗口内，实测 36s） | 标准 3 |
| `e2e/e04-net-probe-backup.spec.js` | 断链矩阵红+告警+回绿、备份 diff 检出 | 网络模块 |
| `e2e/e05-doc-deliverables.spec.js` | 文档产物链完整 | 标准 4/5 |

**注意**：e02/e03/e04 会向实验环境注入真实故障（停服务/填磁盘/停节点），用例自带恢复与
afterAll 兜底；跑完环境自动回健康基线。

## 怎么加新用例（下一个模块照做）

1. 新建 `tests/acceptance/<模块>/acNN-xxx.spec.js`，文件头注释块写清：验证什么、对应哪条验收标准、前置条件
2. 登录用现成的 `require('../helpers/login')`，不要自己写登录；需要 API 对照用 `helpers/spug-api.js`；写 ACE 编辑器用 `helpers/ace.js`
3. **选择器必须基于真实 DOM，不凭想象**：先用探查脚本看页面结构——
   - `node probe.js`：登录后逐页 dump 页面文本与输入框/按钮结构
   - `node probe-ans.js`：Ansible 三页结构（菜单/主机选择下拉/表格）
   - `node probe-batch-exec.js`：批量执行全流程交互示范（选主机→执行→结果）
4. 页面上同文本多元素常见（菜单+面包屑），locator 断言普遍加 `.first()`；仍报 strict mode violation 就用 `error-context.md` 里的快照精确化
5. 探查 dump 的整页文本流会掩盖元素边界（例：「Ansible 执行历史」dump 出来像独立行「执行历史」，exact 匹配落空）——标志文本断言优先用子串匹配
6. 跑通后在本地验收账本登记（不入库）

## 已知行为与坑（写用例前先读）

- **串行执行**（`workers: 1`）：用例共享同一平台、同一账号，批量执行/发布互相影响，别开并行
- 平台验证码字段非必填（密码登录不校验），留空即可
- 命令编辑器是 ace editor：先点 `.ace_content` 再 `page.keyboard.type()`
- **ACE 编辑器写入必须用 `helpers/ace.js`（insertText 整段插入）**：逐键 type 会触发 ACE
  auto-indent，与 YAML 手写空格叠加导致缩进翻倍、语法校验必挂；新建弹窗还自带默认模板，先全选删除
- **exec 页模块名/参数框出厂预填示例值**（"shell" / "hostname"）：只覆盖一个框时另一个的
  残留值会让 ping 报 "has extra params"——两个框都显式 fill
- **antd modal 关闭后 DOM 仍留在页面上**：Close 按钮定位会命中隐藏元素报 not visible，
  关弹窗优先用 Escape；行内展开（expanded-row）会插队 tr 序列，行定位用 `tr.ant-table-row` 过滤
- Web 终端开会话靠**双击**主机树叶子（单击无反应）；xterm 文本用 `evaluate` 直查 `.xterm-rows`，别用常规 locator
- **涉及环境的用例（e2e）开头先 `ensureLabHealthy()`**（helpers/lab-env.js）：前序用例的注入残留会让幂等/全绿断言失真（实测教训：nginx 残留 stopped 导致重跑 changed=1——那是 ansible 在正确修正漂移）
- **`pkill -f http.server` 自杀陷阱**：模式会匹配承载它的 `sh -c` 自身并杀掉，后续命令全部不执行——用 `pkill -f '[h]ttp.server'` 绕开
- **报警历史/探测矩阵/巡检报表页是静态 DOM**：数据晚于页面加载入库时页面不会自己更新，poll 等待必须带 `page.reload()`
- **容器重启后 sysctl 运行时值回默认**：幂等断言用「二连跑第二次 changed=0」口径（第一跑可能是漂移修正，changed>0 属正常）
- **自愈 cooldown（60s）内重复告警被正确抑制**：全量串行跑时自愈用例可能撞上冷却，窗口要放宽（实测 180s 窗口内必恢复）
- ac05 开头的 5 秒缓冲：前一条批量执行刚建立 7 条 SSH 通道，节点 sshd 在旧连接回收前会丢弃新连接（MaxStartups 行为）；`retries: 1` 是兜底
- 凭据在 `config.js`（与 RUNBOOK 一致的本地实验默认值，勿用于公网）
