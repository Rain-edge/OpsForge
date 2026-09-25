/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 * OpsForge：Ansible 执行页——发起 ad-hoc 任务并经 WebSocket 实时回传输出；
 * 增加 Playbook 执行入口（?playbook=<id> 从管理页跳转直达）。
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Card, Input, message, Radio, Select, Space, Tag } from 'antd';
import { PlayCircleOutlined, StopOutlined } from '@ant-design/icons';
import { http, X_TOKEN, t } from 'libs';
import style from '../log.module.less';

const STATUS_COLORS = {
  pending: 'default',
  running: 'blue',
  success: 'green',
  partial: 'orange',
  failed: 'red',
};

export default function Index(props) {
  // Playbook 管理页可带 ?playbook=<id> 跳入，直达 playbook 执行模式
  const params = new URLSearchParams(props.location.search);
  const [execType, setExecType] = useState(params.get('playbook') ? 'playbook' : 'adhoc');
  const [hosts, setHosts] = useState([]);
  const [hostIds, setHostIds] = useState([]);
  const [playbooks, setPlaybooks] = useState([]);
  const [playbookId, setPlaybookId] = useState(
    params.get('playbook') ? Number(params.get('playbook')) : undefined);
  const [moduleName, setModuleName] = useState('shell');
  const [moduleArgs, setModuleArgs] = useState('hostname');
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [summary, setSummary] = useState(null);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState([]);
  const socketRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => {
    http.get('/api/host/').then(res => setHosts(res)).catch(() => null);
    http.get('/api/ansible_ops/playbooks/').then(res => setPlaybooks(res)).catch(() => null);
    return () => {
      if (socketRef.current) socketRef.current.close();
    };
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  function connect(id) {
    setJobId(id);
    // WS URL 拼接沿用 spug 前端既有约定（见 layout/Notification.js）
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(
      `${protocol}//${window.location.host}/api/ws/ansible_ops/jobs/${id}/?x-token=${X_TOKEN}`);
    socketRef.current = socket;
    socket.onmessage = e => {
      try {
        const event = JSON.parse(e.data);
        if (event.type === 'log') {
          setLines(prev => [...prev, event]);
        } else if (event.type === 'done') {
          setStatus(event.status);
          setSummary(event.summary);
          setRunning(false);
          socket.close();
        }
      } catch (err) {
        // 忽略非 JSON 帧（pong 等）
      }
    };
    socket.onclose = () => {
      if (socketRef.current === socket) socketRef.current = null;
    };
    socket.onerror = () => {
      setRunning(false);
      // 连接断开不代表任务结束：提示用户去历史页查看，终止入口随之隐藏但任务仍可从详情页观察
      message.warning(t('实时输出通道断开，任务可能仍在执行，可到执行历史查看结果'));
    };
  }

  function handleSubmit() {
    if (hostIds.length === 0) return message.warning('请选择执行主机');
    const body = {host_ids: hostIds, extra_vars: {}};
    if (execType === 'playbook') {
      if (!playbookId) return message.warning('请选择 Playbook');
      body.playbook_id = playbookId;
    } else {
      body.raw_module = {name: moduleName, args: moduleArgs};
    }
    setRunning(true);
    setLines([]);
    setStatus(null);
    setSummary(null);
    // exec 响应体形如 {"data": {"job_id": n}, "error": ""}，spug 统一 http 封装
    // 自动解包 data（拦截器语义），故 then 直接拿 res.job_id；后端返回 200
    // （约定偏差 202→200 见问题 2），封装对非 200 会自行报错并 reject
    http.post('/api/ansible_ops/exec/', body)
      .then(res => connect(res.job_id)).catch(() => setRunning(false));
  }

  function handleKill() {
    http.post(`/api/ansible_ops/jobs/${jobId}/kill/`)
      .then(() => message.success('已发送终止指令，等待任务退出'))
      .catch(() => null);
  }

  return (
    <Card title={t('Ansible 执行')} style={{margin: 16}}>
      <Space direction="vertical" style={{width: '100%'}} size={12}>
        <Space wrap>
          <Radio.Group value={execType}
                       onChange={e => setExecType(e.target.value)}
                       optionType="button" buttonStyle="solid"
                       options={[
                         {label: t('ad-hoc 模块'), value: 'adhoc'},
                         {label: t('Playbook'), value: 'playbook'},
                       ]}/>
          <Select
            mode="multiple"
            allowClear
            placeholder={t('选择目标主机')}
            style={{minWidth: 360}}
            value={hostIds}
            onChange={setHostIds}
            options={hosts.map(x => ({label: `${x.name} (${x.hostname})`, value: x.id}))}
          />
          {execType === 'playbook' ? (
            <Select
              showSearch
              optionFilterProp="label"
              placeholder={t('选择 Playbook')}
              style={{minWidth: 300}}
              value={playbookId}
              onChange={setPlaybookId}
              options={playbooks.map(x => ({label: x.name, value: x.id}))}
            />
          ) : (
            <>
              <Input
                style={{width: 140}}
                value={moduleName}
                onChange={e => setModuleName(e.target.value)}
                placeholder={t('模块名，如 shell')}
              />
              <Input
                style={{width: 280}}
                value={moduleArgs}
                onChange={e => setModuleArgs(e.target.value)}
                placeholder={t('模块参数，如 sleep 3 && hostname')}
              />
            </>
          )}
          <Button type="primary" icon={<PlayCircleOutlined/>} loading={running} onClick={handleSubmit}>
            {t('执行')}
          </Button>
          {running && (
            <Button danger icon={<StopOutlined/>} onClick={handleKill}>{t('终止')}</Button>
          )}
        </Space>
        {status && (
          <div>
            <Tag color={STATUS_COLORS[status] || 'default'}>
              {status === 'success' ? t('执行成功')
                : status === 'partial' ? t('部分成功')
                : status === 'failed' ? t('执行失败') : status}
            </Tag>
            {summary && Object.keys(summary).map(key => (
              <Tag key={key} color={key === 'ok' ? 'green' : key === 'changed' ? 'cyan' : 'red'}>
                {key}: {summary[key]}
              </Tag>
            ))}
          </div>
        )}
        <div>
          <pre ref={logRef} className={style.log}>
            {lines.map((x, i) => <div key={i}>{x.line || '\u00a0'}</div>)}
          </pre>
          <div style={{marginTop: 4, color: '#999'}}>
            {jobId ? `Job #${jobId} · ${t('实时输出')} · ${lines.length} ${t('行')}` : t('提交任务后此处实时滚动输出')}
          </div>
        </div>
      </Space>
    </Card>
  );
}
