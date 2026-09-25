/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 * OpsForge：Ansible 执行历史——任务列表与日志回放
 * （日志回放复用执行页同一条 WS 通道：对已终态任务，consumer 补发全量日志后即关闭）
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Card, Modal, Space, Table, Tag } from 'antd';
import { http, X_TOKEN, t } from 'libs';
import style from '../log.module.less';

const STATUS = {
  pending: {color: 'default', text: '等待执行'},
  running: {color: 'blue', text: '执行中'},
  success: {color: 'green', text: '成功'},
  partial: {color: 'orange', text: '部分成功'},
  failed: {color: 'red', text: '失败'},
};

export default function Index() {
  const [jobs, setJobs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);
  const [lines, setLines] = useState([]);
  const socketRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => {
    setLoading(true);
    http.get(`/api/ansible_ops/jobs/?page=${page}&page_size=${pageSize}`)
      .then(res => {
        setJobs(res.jobs);
        setTotal(res.total);
      })
      .finally(() => setLoading(false));
  }, [page, pageSize]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  useEffect(() => () => {
    if (socketRef.current) socketRef.current.close();
  }, []);

  function showDetail(job) {
    setDetail(job);
    setLines([]);
    http.get(`/api/ansible_ops/jobs/${job.id}/`).then(res => setDetail(res)).catch(() => null);
    openLog(job.id);
  }

  function openLog(id) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(
      `${protocol}//${window.location.host}/api/ws/ansible_ops/jobs/${id}/?x-token=${X_TOKEN}`);
    socketRef.current = socket;
    socket.onmessage = e => {
      try {
        const event = JSON.parse(e.data);
        if (event.type === 'log') {
          setLines(prev => [...prev, event.line]);
        } else if (event.type === 'done') {
          socket.close();
        }
      } catch (err) {
        // 忽略非 JSON 帧
      }
    };
    socket.onclose = () => {
      if (socketRef.current === socket) socketRef.current = null;
    };
  }

  function closeDetail() {
    setDetail(null);
    if (socketRef.current) socketRef.current.close();
  }

  function renderSummary(record) {
    const s = record.summary;
    if (s && s.ok !== undefined) {
      return (
        <Space size={0} wrap>
          <Tag color="green">ok {s.ok}</Tag>
          <Tag color="cyan">changed {s.changed}</Tag>
          {s.unreachable > 0 && <Tag color="orange">unreachable {s.unreachable}</Tag>}
          {s.failed > 0 && <Tag color="red">failed {s.failed}</Tag>}
        </Space>
      );
    }
    if (s && s.reason) return <Tag color="red">reason: {s.reason}</Tag>;
    if (s && s.error) return <Tag color="red">{s.error}</Tag>;
    return '-';
  }

  const columns = [
    {title: 'ID', dataIndex: 'id', width: 70},
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: v => <Tag color={STATUS[v] ? STATUS[v].color : 'default'}>{STATUS[v] ? STATUS[v].text : v}</Tag>,
    },
    {title: '目标主机', dataIndex: 'host_ids', width: 90, render: v => `${v.length} 台`},
    {
      title: '执行内容', key: 'raw',
      render: (_, r) => r.raw_module
        ? `${r.raw_module.name}${r.raw_module.args ? ' ' + r.raw_module.args : ''}`
        : `playbook #${r.playbook_id || '-'}`,
    },
    {title: '结果摘要', key: 'summary', render: renderSummary},
    {title: '开始时间', dataIndex: 'created_at', width: 165},
    {title: '结束时间', dataIndex: 'finished_at', width: 165, render: v => v || '-'},
    {
      title: '操作', key: 'action', width: 80,
      render: (_, r) => <Button type="link" size="small" onClick={() => showDetail(r)}>{t('详情')}</Button>,
    },
  ];

  return (
    <Card title={t('Ansible 执行历史')} style={{margin: 16}}>
      <Table
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={jobs}
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
        title={detail ? `任务 #${detail.id} ${t('详情')}` : ''}
        visible={!!detail}
        onCancel={closeDetail}
        footer={null}
        width={780}
      >
        {detail && (
          <div>
            <p style={{marginBottom: 8}}>
              <Tag color={STATUS[detail.status] ? STATUS[detail.status].color : 'default'}>
                {STATUS[detail.status] ? STATUS[detail.status].text : detail.status}
              </Tag>
              <span>主机：{(detail.hosts || []).map(x => x.name).join('、') || '...'}</span>
            </p>
            <pre ref={logRef} className={style.log}>
              {lines.map((line, i) => <div key={i}>{line || '\u00a0'}</div>)}
            </pre>
          </div>
        )}
      </Modal>
    </Card>
  );
}
