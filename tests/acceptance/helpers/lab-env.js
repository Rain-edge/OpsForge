// 实验环境健康守护（e2e 用例共用）
//
// 背景：故障注入类用例（e02/e03/e04）会临时破坏环境；前序用例的残留状态会让
// 后序用例的幂等断言失真（实测教训：web-02 nginx 残留 stopped 导致 e01 的
// nginx playbook 重跑 changed=1——那是 ansible 在正确修正漂移，不是不幂等）。
// 因此每条涉及环境的用例开头先收敛环境，结尾兜底恢复。
//
// ⚠️ pkill 自杀陷阱：`pkill -f http.server` 会匹配到承载它的 `sh -c "...http.server..."`
// 自身并把 shell 杀掉，导致后面的命令全部不执行（实测踩中）。用 [h]ttp 正则技巧
// 绕开：模式匹配 "http.server" 字面，但 shell 命令行里是 "[h]ttp.server" 不自匹配。
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const exec = promisify(execFile);
const dex = (container, cmd) => exec('docker', ['exec', container, 'sh', '-c', cmd], { timeout: 30_000 });

// 清除全部注入故障，把环境拉回健康基线（幂等，可重复调用；单条失败不阻塞其余）
async function clearInjectedFaults() {
  await dex('web-01', 'systemctl start nginx').catch(() => {});
  await dex('web-02', "pkill -f '[h]ttp.server'; sleep 1; systemctl start nginx").catch(() => {});
  await dex('db-01', 'umount /mnt/fault 2>/dev/null; rmdir /mnt/fault 2>/dev/null; true').catch(() => {});
}

// 等待环境真正健康（清故障 + 状态断言），e01 这类幂等断言用例的必要前置
async function ensureLabHealthy() {
  await clearInjectedFaults();
  for (const [container, service] of [['web-01', 'nginx'], ['web-02', 'nginx']]) {
    for (let i = 0; i < 10; i++) {
      try {
        const { stdout } = await dex(container, `systemctl is-active ${service}`);
        if (stdout.trim() === 'active') break;
      } catch { /* inactive 时退出码非 0 */ }
      await new Promise(r => setTimeout(r, 2000));
    }
  }
  // 80 端口必须由 nginx 持有（python3 占用残留会让巡检/探测断言失真）
  await dex('web-02', "pkill -f '[h]ttp.server' 2>/dev/null; true").catch(() => {});
}

module.exports = { clearInjectedFaults, ensureLabHealthy, dex };
