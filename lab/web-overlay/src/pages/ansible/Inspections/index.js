/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 * OpsForge：批量巡检报表——历史轮次列表 + HTML 报告内嵌预览 + 产物下载。
 * 报告文件视图走 ?x-token= 查询参数鉴权（libs/middleware.py 支持，同 WS 先例）。
 */
import React, { useEffect, useState } from 'react';
import { Button, Card, Modal, Space, Table, Tag, Tooltip } from 'antd';
import { http, X_TOKEN, t } from 'libs';

const STATUS = {
  running: {color: 'blue', text: '巡检中'},
  success: {color: 'green', text: '完成'},
  partial: {color: 'orange', text: '部分完成'},
  failed: {color: 'red', text: '失败'},
};
const TRIGGER = {manual: '手动触发', scheduled: '定时巡检'};

export default function Index() {
  const [reports, setReports] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null);
  // 有进行中的轮次时定时刷新列表（手动触发后页面停留即可看到结果落定）
  const [tick, setTick] = useState(0);
  const [running, setRunning] = useState(false);

  function runNow() {
    setRunning(true);
    http.post('/api/ansible_ops/inspection/run/')
      .then(() => {
        setPage(1);
        setTick(t => t + 1);
      })
      .finally(() => setRunning(false));
  }

  useEffect(() => {
    setLoading(true);
    let timer = null;
    http.get(`/api/ansible_ops/inspections/?page=${page}&page_size=${pageSize}`)
      .then(res => {
        setReports(res.reports);
        setTotal(res.total);
        if (res.reports.some(x => x.status === 'running')) {
          timer = setTimeout(() => setTick(t => t + 1), 3000);
        }
      })
      .finally(() => setLoading(false));
    return () => timer && clearTimeout(timer);
  }, [page, pageSize, tick]);

  function fileUrl(record, kind, download) {
    let url = `/api/ansible_ops/inspections/${record.id}/files/${kind}/?x-token=${X_TOKEN}`;
    return download ? url + '&dl=1' : url;
  }

  function renderCounts(record) {
    if (record.status === 'failed') {
      return <Tooltip title={record.summary.error || '巡检失败'}><Tag color="red">失败</Tag></Tooltip>;
    }
    if (record.status === 'running') return <Tag color="blue">巡检中...</Tag>;
    return (
      <Space size={0} wrap>
        <Tag color="green">正常 {record.total_ok}</Tag>
        {record.total_warn > 0 && <Tag color="orange">警告 {record.total_warn}</Tag>}
        {record.total_crit > 0 && <Tag color="red">异常 {record.total_crit}</Tag>}
      </Space>
    );
  }

  const columns = [
    {title: 'ID', dataIndex: 'id', width: 60},
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: v => <Tag color={STATUS[v] ? STATUS[v].color : 'default'}>{STATUS[v] ? STATUS[v].text : v}</Tag>,
    },
    {title: '触发方式', dataIndex: 'trigger', width: 100, render: v => TRIGGER[v] || v},
    {title: '结果统计', key: 'counts', render: renderCounts},
    {title: '主机数', dataIndex: 'hosts_total', width: 70, render: v => `${v} 台`},
    {title: '触发人', dataIndex: 'user', width: 90, render: v => v || '-'},
    {title: '开始时间', dataIndex: 'created_at', width: 160},
    {title: '结束时间', dataIndex: 'finished_at', width: 160, render: v => v || '-'},
    {
      title: '操作', key: 'action', width: 200,
      render: (_, r) => (r.status === 'success' || r.status === 'partial') ? (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => setPreview(r)}>预览报告</Button>
          <Button type="link" size="small" href={fileUrl(r, 'xlsx', true)}>Excel</Button>
          <Button type="link" size="small" href={fileUrl(r, 'html', true)}>HTML</Button>
        </Space>
      ) : '-',
    },
  ];

  return (
    <Card
      title={t('批量巡检报表')}
      style={{margin: 16}}
      extra={
        <Button type="primary" size="small" loading={running} onClick={runNow}>
          {t('立即巡检')}
        </Button>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={reports}
        pagination={{
          total,
          current: page,
          pageSize,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
          showTotal: n => `共 ${n} 条`,
        }}
      />
      <Modal
        title={preview ? `巡检报告 #${preview.id}` : ''}
        visible={!!preview}
        onCancel={() => setPreview(null)}
        footer={null}
        width={900}
        destroyOnClose
      >
        {preview && (
          <iframe
            title="inspection-report"
            src={fileUrl(preview, 'html')}
            style={{width: '100%', height: 520, border: 'none'}}
          />
        )}
      </Modal>
    </Card>
  );
}
