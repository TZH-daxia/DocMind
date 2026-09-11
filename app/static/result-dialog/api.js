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

// 提交前校验：港口转三字码 + 委托客户存在性（真实提交由后端后续接入）
export async function validateSubmission(taskId, payload) {
  return request(
    `${BASE_URL}/tasks/${encodeURIComponent(taskId)}/submission/validate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
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
