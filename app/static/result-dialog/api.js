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

async function request(url) {
  const response = await fetch(url);
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
