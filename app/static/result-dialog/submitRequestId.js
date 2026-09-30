/**
 * 提交订单的幂等键（request_id）：同一票订单的多次重试必须带同一把键，
 * 后端据此回放首次结果、不再重复下单（见 app/service/order_submit_service.py 的 submit）。
 *
 * 键**按任务持久化**到 localStorage：提交请求超时后，用户切走再切回、甚至刷新页面
 * 再点「提交」，仍是同一次尝试的重试，不会在后端被当成一张新单。
 *
 * 何时作废（换新键）由调用方根据后端返回的 `retryable` 决定：
 * - 后端确认「本次尝试已有定论」（成功 / 被 poOrder 明确拒绝）→ 作废，下次点击是全新提交；
 * - 结果未知（超时 / 正在提交中）→ 保留，重试始终打在同一把键上。
 * 传输层直接报错（连响应都没拿到）时同样保留，这正是最需要防重复建单的情形。
 */

const STORAGE_KEY = "docmind.submitRequestIds";
// 只保留最近若干条，避免长期使用后 localStorage 无限增长
const MAX_REMEMBERED = 200;

function readAll() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      return raw;
    }
  } catch {
    // 隐私模式 / 数据损坏：退化成"本次会话内没有键"
  }
  return {};
}

function writeAll(map) {
  const entries = Object.entries(map).slice(-MAX_REMEMBERED);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch {
    // 写失败不影响提交流程，只是刷新后重试会换成新键
  }
}

/** 读某任务当前的幂等键；没有则返回空串。 */
export function loadSubmitRequestId(taskId) {
  if (!taskId) {
    return "";
  }
  return String(readAll()[String(taskId)] || "");
}

/** 记住某任务的幂等键，并原样返回它，方便 `load(...) || remember(...)` 连用。 */
export function rememberSubmitRequestId(taskId, requestId) {
  if (taskId && requestId) {
    const all = readAll();
    all[String(taskId)] = String(requestId);
    writeAll(all);
  }
  return requestId;
}

/** 作废某任务的幂等键（本次尝试已有定论时调用）：下次点击会生成新的键。 */
export function clearSubmitRequestId(taskId) {
  if (!taskId) {
    return;
  }
  const all = readAll();
  if (!(String(taskId) in all)) {
    return;
  }
  delete all[String(taskId)];
  writeAll(all);
}

/** 生成一把新的幂等键；优先用 crypto.randomUUID，环境不支持时回退到时间戳 + 随机串。 */
export function newSubmitRequestId() {
  try {
    if (window.crypto?.randomUUID) {
      return window.crypto.randomUUID();
    }
  } catch {
    // 非安全上下文等场景下 randomUUID 不可用：走下面的兜底
  }
  return `submit_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}
