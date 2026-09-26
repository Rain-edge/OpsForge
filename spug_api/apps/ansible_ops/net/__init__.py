# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
# OpsForge：网络探测与设备备份包。
#
# 链路（run_net 常驻命令调度，supervisor program opsforge-net）：
#   探测轮 probe.run_round        —— asyncio 并发探测目标矩阵（≤50 并发），
#                                    矩阵快照落库 + 断链状态机推进
#   备份轮 backup.run_backup_round —— netmiko SSH+vtysh 采集设备运行配置，
#                                    diff 相邻版本，变更落库 NetConfigChange
# 断链告警复用 ALERT 告警通道：连续 N 次失败 Alarm.objects.create(status='1')，
# 恢复落 status='2'；经 heal 的 post_save(Alarm) 挂点走同一链路——本模块的
# 告警名不匹配任何自愈规则关键字时自然旁路（不误触发自愈，见 probe._alarm_name）。
