import { currentTicket } from "./currentUser.js";

// 与后端 Settings.api_prefix + router 前缀保持一致（见 app/config.py）
const BASE_URL = "/docmind/analysis";

export function pageImageUrl(taskId, pageNo) {
  return `${BASE_URL}/tasks/${encodeURIComponent(taskId)}/pages/${pageNo}`;
}

export async function fetchTaskStatus(taskId) {
  return request(`${BASE_URL}/tasks/${encodeURIComponent(taskId)}`);
}

export async function fetchResult(taskId) {
  return request(`${BASE_URL}/tasks/${encodeURIComponent(taskId)}/result`);
}

// 最近文件列表：供弹窗头部的多文件快速切换（前端只取已解析完成的）
export async function fetchRecentFiles() {
  return request(`${BASE_URL}/files`);
}

// 委托客户主数据搜索：供"准备提交"弹窗的委托客户下拉选择
export async function searchCustomers(keyword) {
  return request(
    `${BASE_URL}/customers?keyword=${encodeURIComponent(keyword)}`,
  );
}

// 港口主数据搜索：供"准备提交"弹窗的始发港/目的港下拉选择三字码
export async function searchPorts(keyword) {
  return request(`${BASE_URL}/ports?keyword=${encodeURIComponent(keyword)}`);
}

// 本票客户客服联系人：选完委托客户后取候选，供「本票客户客服联系人」展示与挑选。
// area / system 用于判断哪条是本票默认联系人（口径同 poOrder 的 defaultlxrjson）
export async function fetchContacts(fid, area = "", system = "") {
  const params = `fid=${encodeURIComponent(fid)}`;
  const site = area ? `&area=${encodeURIComponent(area)}` : "";
  const biz = system ? `&system=${encodeURIComponent(system)}` : "";
  return request(`${BASE_URL}/contacts?${params}${site}${biz}`);
}

// 唯凯站点字典：供工具条「委托唯凯站点」下拉（字典全量、按分组返回，不做关键字过滤）
export async function fetchSites() {
  return request(`${BASE_URL}/sites`);
}

// 用户默认设置：poOrder 用户设置模板（type=110）里的订单新增默认值，
// 供工具条预填站点 / 服务方式 / 运输种类。取不到时各项为空串，前端用内置兜底值；
// 票据由 ticketHeaders() 统一放在请求头（后端据此调 poOrder 的 api/UserTemplet）
export async function fetchUserDefaults(logname) {
  const params = logname ? `?logname=${encodeURIComponent(logname)}` : "";
  return request(`${BASE_URL}/user-defaults${params}`);
}

// 委托客户的信用等级与信控提示：选完客户后显示在其输入框下方。
// area 传当前站点（信控按站点分别校验），返回 { enabled, level, message, hint }
export async function fetchCredit(fid, area = "", system = "") {
  const params = `fid=${encodeURIComponent(fid)}`;
  const site = area ? `&area=${encodeURIComponent(area)}` : "";
  const biz = system ? `&system=${encodeURIComponent(system)}` : "";
  return request(`${BASE_URL}/credit?${params}${site}${biz}`);
}

// 项目候选：按委托客户查（项目归属客户），keyword 非空时按名称/编码过滤。
// **不按站点过滤**（同 poOrder）：站点权限由前端在选中项目之后判定，
// 先过滤会把"没有权限"的证据滤掉，那句「该项目没有X站点权限！」就永远不会出现
export async function fetchProjects(fid, keyword = "") {
  const customer = `fid=${encodeURIComponent(fid)}`;
  const text = keyword ? `&keyword=${encodeURIComponent(keyword)}` : "";
  return request(`${BASE_URL}/projects?${customer}${text}`);
}

// 提交订单：把弹窗核对后的表单与订单上下文交给后端组装报文（固定值、system 派生规则
// 都在后端，见 app/service/order_submit_service.py），返回 { ok, order_code, message }
export async function submitOrder(body) {
  return request(`${BASE_URL}/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * poOrder 票据统一走**请求头**，不放 URL：地址栏里的 ticket 会跟着进浏览器历史、
 * `Referer` 与网关访问日志，等于把登录凭据写进日志。
 *
 * 开发期跨端口调试读不到 poOrder 的 storage，仍可用 `?ticket=` 兜底 —— 读取顺序在
 * `currentUser.js`（URL 参数优先，其次 Cookie），认不认 URL 里那个由后端
 * `DOCMIND_ALLOW_URL_TICKET` 决定（默认关闭）。
 */
function ticketHeaders() {
  const ticket = currentTicket();
  return ticket ? { Authorization: ticket } : {};
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { ...ticketHeaders(), ...(options.headers || {}) },
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败：${response.status}`);
  }
  return payload;
}
