// 平台 API 客户端：UI 断言需要后端对照时的取数通道
// 鉴权：POST /api/account/login/ 拿 access_token，之后请求带 x-token header（探查实证）
// 响应坑（RUNBOOK 坑 5/8）：JSON 冒号后带空格；成功时 error 多为 "" 但部分接口为 null
const { baseUrl, admin } = require('../config');

async function apiLogin(request) {
  const resp = await request.post(baseUrl + '/api/account/login/', {
    data: { username: admin.username, password: admin.password, type: 'default' },
  });
  const body = await resp.json();
  if (!body.data || !body.data.access_token) {
    throw new Error('API 登录失败: ' + JSON.stringify(body).slice(0, 200));
  }
  return body.data.access_token;
}

// GET 单端点并解包 data；失败直接抛错（用例里当断言前置用）
async function apiGet(request, token, path) {
  const resp = await request.get(baseUrl + path, { headers: { 'x-token': token } });
  const body = await resp.json();
  if (body.error && body.error !== '') {
    throw new Error(`GET ${path} 失败: ${body.error}`);
  }
  return body.data;
}

// DELETE（playbook 清理用）
async function apiDelete(request, token, path) {
  const resp = await request.delete(baseUrl + path, { headers: { 'x-token': token } });
  const body = await resp.json();
  if (body.error && body.error !== '') {
    throw new Error(`DELETE ${path} 失败: ${body.error}`);
  }
  return body.data;
}

module.exports = { apiLogin, apiGet, apiDelete };
