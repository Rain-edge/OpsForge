# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：告警闭环自愈——规则匹配、自愈执行、复检判定与结果外发。
#
# 触发链（任务约定固定）：spug monitor 告警挂钩（monitor/utils.py 告警分支
# 最小侵入 4 行）→ 规则匹配 → 冷却/进行中去重 → 自愈前复检确认 → 修复
# Playbook（复用执行引擎引擎）→ 自愈后复检（INSP 单项插件）→ 恢复判定
# → webhook 外发（含主机/指标/动作/结果/耗时）。全链路事件落 HealEvent。
#
# 红线：本包运行在 monitor worker 进程内，任何环节异常必须吞噬或落库，
# 绝不影响监控告警主链路。
