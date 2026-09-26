/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 * OpsForge：网络探测与设备备份——绿/红探测矩阵、手动探测、
 * 设备配置版本回溯与变更 diff。
 */
import React, {useEffect, useState} from 'react';
import {Button, Card, Empty, Modal, Space, Table, Tag, Tooltip} from 'antd';
import {http, t} from 'libs';

export default function Index() {
  const [run, setRun] = useState(null);
  const [backup, setBackup] = useState(null);
  const [loading, setLoading] = useState(false);
  const [probing, setProbing] = useState(false);
  const [diffView, setDiffView] = useState(null);
  const [fileView, setFileView] = useState(null);

  function loadLatest() {
    setLoading(true);
    http.get('/api/ansible_ops/net-probe/latest/')
      .then(res => setRun(res.run))
      .finally(() => setLoading(false));
  }

  function loadBackup() {
    http.get('/api/ansible_ops/netbackup/')
      .then(res => setBackup(res));
  }

  useEffect(() => {
    loadLatest();
    loadBackup();
  }, []);

  function runNow() {
    setProbing(true);
    http.post('/api/ansible_ops/net-probe/run/')
      .then(res => setRun(res))
      .finally(() => setProbing(false));
  }

  function viewFile(device, ts) {
    http.get(`/api/ansible_ops/netbackup/${device}/files/${ts}/`)
      .then(res => setFileView(res));
  }

  // 矩阵数据整形：cells（目标×检查项平铺）→ 行=目标、列=检查项
  const targets = [];
  const targetIndex = {};
  const checks = [];
  ((run && run.cells) || []).forEach(c => {
    if (!(c.target in targetIndex)) {
      targetIndex[c.target] = {name: c.target, cells: {}};
      targets.push(targetIndex[c.target]);
    }
    targetIndex[c.target].cells[c.check] = c;
    if (!checks.includes(c.check)) checks.push(c.check);
  });

  const matrixColumns = [
    {title: '目标', dataIndex: 'name', width: 120},
    ...checks.map(check => ({
      title: check === 'icmp' ? 'ICMP' : check.replace('tcp:', 'TCP '),
      key: check,
      render: (_, r) => {
        const cell = r.cells[check];
        if (!cell) return '-';
        if (cell.status === 'ok') {
          return <Tag color="green">通{cell.latency_ms != null ? ` ${cell.latency_ms}ms` : ''}</Tag>;
        }
        return <Tooltip title={cell.detail}><Tag color="red">断</Tag></Tooltip>;
      },
    })),
  ];

  const changeColumns = [
    {title: '设备', dataIndex: 'device', width: 90},
    {title: '版本变更', key: 'versions', render: r => `${r.from_ts} → ${r.to_ts}`},
    {title: '检出时间', dataIndex: 'detected_at', width: 170},
    {
      title: '操作', key: 'action', width: 190,
      render: (_, r) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => setDiffView(r)}>查看 diff</Button>
          <Button type="link" size="small" onClick={() => viewFile(r.device, r.to_ts)}>查看新版本</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title={t('网络探测矩阵')}
        style={{margin: 16}}
        extra={
          <Space>
            {run && <span style={{color: '#999'}}>{`#${run.id} ${run.created_at} · 通 ${run.total_ok} / 断 ${run.total_fail}`}</span>}
            <Button type="primary" size="small" loading={probing} onClick={runNow}>
              {t('立即探测')}
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="name"
          loading={loading}
          columns={matrixColumns}
          dataSource={targets}
          pagination={false}
          locale={{emptyText: <Empty description="暂无探测记录，点击右上角立即探测"/>}}
        />
      </Card>
      <Card title={t('设备配置备份')} style={{margin: 16}}>
        {backup && backup.devices.map(d => (
          <div key={d.name} style={{marginBottom: 8}}>
            <b>{d.name}</b>（{d.host}）：共 {d.versions.length} 个版本
            {d.versions.length > 0 && (
              <Button type="link" size="small" onClick={() => viewFile(d.name, d.versions[0])}>
                查看最新 {d.versions[0]}
              </Button>
            )}
          </div>
        ))}
        <Table
          rowKey="id"
          style={{marginTop: 8}}
          columns={changeColumns}
          dataSource={backup ? backup.changes : []}
          pagination={{pageSize: 5, showTotal: n => `共 ${n} 条变更`}}
          locale={{emptyText: <Empty description="暂无配置变更记录"/>}}
        />
      </Card>
      <Modal
        title={diffView ? `${diffView.device} 配置 diff（${diffView.from_ts} → ${diffView.to_ts}）` : ''}
        visible={!!diffView}
        onCancel={() => setDiffView(null)}
        footer={null}
        width={860}
        destroyOnClose
      >
        {diffView && <pre style={{maxHeight: 480, overflow: 'auto', background: '#fafafa', padding: 12}}>{diffView.diff || '（无差异）'}</pre>}
      </Modal>
      <Modal
        title={fileView ? `${fileView.device} 配置 @ ${fileView.name}` : ''}
        visible={!!fileView}
        onCancel={() => setFileView(null)}
        footer={null}
        width={860}
        destroyOnClose
      >
        {fileView && <pre style={{maxHeight: 480, overflow: 'auto', background: '#fafafa', padding: 12}}>{fileView.content}</pre>}
      </Modal>
    </div>
  );
}
