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

async function request(url, options = {}) {
  const response = await fetch(url, options);
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
