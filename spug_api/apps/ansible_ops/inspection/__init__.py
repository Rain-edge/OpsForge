# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：批量巡检——检查插件协议与注册表。
#
# 协议（任务约定固定）：check(runner, host) -> CheckResult(name, status, value, message)
#   status ∈ ok | warn | crit；新增检查只需在 checks.py 写函数并 @register
# 执行通道：复用执行引擎引擎的 raw/shell 通道与动态 inventory（见 collect.py），
#   不另起 SSH 连接；6 项内置检查合并为一条采集脚本，一轮只发一次 ansible
from collections import namedtuple

# 检查结果状态，报告端按此着色（crit 红 / warn 橙 / ok 绿）
STATUS_OK, STATUS_WARN, STATUS_CRIT = 'ok', 'warn', 'crit'

CheckResult = namedtuple('CheckResult', ['check', 'host', 'status', 'value', 'message'])

# 注册表 {检查名: fn(runner, host) -> CheckResult}（约定：新增检查只需注册）
CHECKS = {}


def register(name):
    """检查注册装饰器：同名重复注册视为配置错误，直接抛错（fail fast）。"""
    def decorator(fn):
        if name in CHECKS:
            raise ValueError(f'巡检检查项重名: {name}')
        CHECKS[name] = fn
        return fn
    return decorator
