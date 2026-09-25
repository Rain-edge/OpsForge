/**
 * Copyright (c) OpenSpug Organization. https://github.com/openspug/spug
 * Copyright (c) <spug.dev@gmail.com>
 * Released under the AGPL-3.0 License.
 * OpsForge：Playbook 管理页——增删改查、在线编辑 YAML、git 版本历史与回滚。
 * 保存即触发 ansible-playbook --syntax-check，通过后自动产生 git 提交（后端 repo.py）；
 * diff 以 patch 文本形式随 versions 接口返回，回滚以"取旧版内容重新提交"实现。
 */
import React, { useEffect, useState } from 'react';
import {
  Button, Card, Input, message, Modal, Popconfirm, Space, Table, Tag, Tooltip
} from 'antd';
import { HistoryOutlined, PlayCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { ACEditor } from 'components';
import { http, history, t } from 'libs';
// 上游 ACEditor 仅注册 sh/text/json/space/python 模式，yaml 在本页按需注册
import 'ace-builds/src-noconflict/mode-yaml';

const EMPTY_FORM = {
  id: null, name: '', description: '',
  content: '- hosts: all\n  tasks:\n    - name: 任务名\n      ping:\n',
};

export default function Index() {
  const [items, setItems] = useState([]);
  const [fetching, setFetching] = useState(false);
  const [form, setForm] = useState(null);          // 编辑对话框数据，null=关闭
  const [saving, setSaving] = useState(false);
  const [versions, setVersions] = useState(null);  // 版本对话框 {id, name, list}，null=关闭
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [expanded, setExpanded] = useState(null);  // 当前展开 diff 的 commit id

  useEffect(() => {
    fetchList();
  }, []);

  function fetchList() {
    setFetching(true);
    http.get('/api/ansible_ops/playbooks/')
      .then(res => setItems(res))
      .catch(() => null)
      .finally(() => setFetching(false));
  }

  function handleSave() {
    if (!form.name.trim()) return message.warning('请输入名称');
    if (!form.content.trim()) return message.warning('请输入 Playbook 内容');
    setSaving(true);
    const request = form.id
      ? http.put(`/api/ansible_ops/playbooks/${form.id}/`, form)
      : http.post('/api/ansible_ops/playbooks/', form);
    request.then(() => {
      message.success(form.id ? '已保存，新版本已提交' : '创建成功');
      setForm(null);
      fetchList();
    }).catch(() => null).finally(() => setSaving(false));
  }

  function handleDelete(record) {
    http.delete(`/api/ansible_ops/playbooks/${record.id}/`)
      .then(() => {
        message.success('已删除');
        fetchList();
      }).catch(() => null);
  }

  function fetchVersions(id, name) {
    setLoadingVersions(true);
    setExpanded(null);
    http.get(`/api/ansible_ops/playbooks/${id}/versions/`)
      .then(res => setVersions({id, name, list: res}))
      .catch(() => setVersions(null))
      .finally(() => setLoadingVersions(false));
  }

  function openVersions(record) {
    setVersions({id: record.id, name: record.name, list: []});
    fetchVersions(record.id, record.name);
  }

  function handleRollback(commit) {
    http.post(`/api/ansible_ops/playbooks/${versions.id}/rollback/`, {commit: commit.id})
      .then(() => {
        message.success(`已回滚到 ${commit.id.slice(0, 7)}（内容已恢复，版本记录保留）`);
        fetchVersions(versions.id, versions.name);
        fetchList();
      }).catch(() => null);
  }

  const columns = [
    {title: 'ID', dataIndex: 'id', width: 60},
    {title: t('名称'), dataIndex: 'name'},
    {title: t('描述'), dataIndex: 'description', ellipsis: true},
    {title: t('更新人'), dataIndex: 'updated_by_name', width: 100},
    {title: t('更新时间'), dataIndex: 'updated_at', width: 150},
    {
      title: t('操作'), width: 300,
      render: info => (
        <Space size={4}>
          <Button size="small" type="link" onClick={() => setForm({...info})}>{t('编辑')}</Button>
          <Button size="small" type="link" icon={<HistoryOutlined/>}
                  onClick={() => openVersions(info)}>{t('版本')}</Button>
          <Tooltip title={t('到执行页发起该 Playbook')}>
            <Button size="small" type="link" icon={<PlayCircleOutlined/>}
                    onClick={() => history.push(`/ansible/exec?playbook=${info.id}`)}>{t('执行')}</Button>
          </Tooltip>
          <Popconfirm title={t('删除后版本历史一并移除，确定删除？')}
                      onConfirm={() => handleDelete(info)}>
            <Button size="small" type="link" danger>{t('删除')}</Button>
          </Popconfirm>
        </Space>
      )
    },
  ];

  return (
    <Card title={t('Playbook 管理')} extra={
      <Button type="primary" icon={<PlusOutlined/>} onClick={() => setForm({...EMPTY_FORM})}>
        {t('新建')}
      </Button>
    } style={{margin: 16}}>
      <Table
        rowKey="id"
        loading={fetching}
        columns={columns}
        dataSource={items}
        pagination={false}
        locale={{emptyText: t('暂无 Playbook，点击右上角新建')}}
      />
      <Modal
        title={form && form.id ? t('编辑 Playbook') : t('新建 Playbook')}
        open={!!form}
        width={720}
        onCancel={() => setForm(null)}
        onOk={handleSave}
        confirmLoading={saving}
        okText={t('保存（自动语法校验 + git 提交）')}
      >
        {form && (
          <Space direction="vertical" style={{width: '100%'}} size={8}>
            <Space>
              <Input style={{width: 280}} placeholder={t('名称，如 nginx 部署')}
                     value={form.name}
                     onChange={e => setForm({...form, name: e.target.value})}/>
              <Input style={{width: 360}} placeholder={t('描述（可选）')}
                     value={form.description}
                     onChange={e => setForm({...form, description: e.target.value})}/>
            </Space>
            <ACEditor
              mode="yaml"
              height="320px"
              value={form.content}
              onChange={value => setForm({...form, content: value})}
            />
          </Space>
        )}
      </Modal>
      <Modal
        title={versions ? `${versions.name} · ${t('版本历史（git）')}` : ''}
        open={!!versions}
        width={860}
        footer={null}
        onCancel={() => setVersions(null)}
      >
        <Table
          rowKey="id"
          loading={loadingVersions}
          dataSource={versions ? versions.list : []}
          pagination={false}
          size="small"
          locale={{emptyText: t('暂无版本记录')}}
          columns={[
            {
              title: t('版本'), width: 90,
              render: info => <Tag>{info.id.slice(0, 7)}</Tag>,
            },
            {title: t('提交说明'), dataIndex: 'message', ellipsis: true},
            {title: t('操作人'), dataIndex: 'author', width: 90},
            {title: t('时间'), dataIndex: 'date', width: 130},
            {
              title: t('操作'), width: 150,
              render: info => (
                <Space size={0}>
                  <Button size="small" type="link"
                          onClick={() => setExpanded(expanded === info.id ? null : info.id)}>
                    {expanded === info.id ? t('收起 diff') : t('查看 diff')}
                  </Button>
                  <Popconfirm title={t('回滚会将该版本内容重新提交为新记录，确定？')}
                              onConfirm={() => handleRollback(info)}>
                    <Button size="small" type="link">{t('回滚到此版')}</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          expandable={{
            expandedRowKeys: expanded ? [expanded] : [],
            onExpand: (isExpand, info) => setExpanded(isExpand ? info.id : null),
            expandedRowRender: info => (
              <pre style={{
                margin: 0, padding: 8, maxHeight: 260, overflow: 'auto',
                background: '#fafafa', fontSize: 12,
              }}>{info.patch || t('（无差异）')}</pre>
            ),
            rowExpandable: () => true,
          }}
        />
      </Modal>
    </Card>
  );
}
