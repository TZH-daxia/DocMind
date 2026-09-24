/**
 * 已提交文件的本地记录：页签条据此把「提交成功」的文件显示成绿色并带对号。
 *
 * 之所以先放本地：真实提交接口还没接入（后端目前只做本地校验），没有可查询的
 * 提交状态；等接口落地后，把 useResultDialog 里调用 rememberSubmittedTask 的
 * 时机挪到提交成功回调即可，页签条的渲染逻辑不用动。
 */

const STORAGE_KEY = "docmind.submittedTasks";
// 只保留最近若干条，避免长期使用后 localStorage 无限增长
const MAX_REMEMBERED = 200;

export function loadSubmittedTaskIds() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
    return new Set(Array.isArray(raw) ? raw.map(String) : []);
  } catch {
    // 隐私模式 / 数据损坏：退化成"本次会话内有效"
    return new Set();
  }
}

export function rememberSubmittedTask(taskId) {
  const ids = loadSubmittedTaskIds();
  ids.add(String(taskId));
  const trimmed = [...ids].slice(-MAX_REMEMBERED);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // 写入失败不影响提交流程，只是刷新后丢失标记
  }
  return new Set(trimmed);
}

// ── 订舱编号 ───────────────────────────────────────────────────────────────
// 提交成功后 poOrder 返回的编号（形如 BOAE2609240001PVG）。按任务单独存一份映射，
// 这样**切换任务、甚至刷新页面**后，弹窗头部（叉号左侧）仍显示该任务的编号。
// 不复用上面的数组：那是 Set 语义的 id 列表，改它的结构会牵动已有本地数据
const ORDER_CODE_KEY = "docmind.orderCodes";

/** 读某任务的订舱编号；没有则返回空串。 */
export function loadOrderCode(taskId) {
  if (!taskId) {
    return "";
  }
  try {
    const raw = JSON.parse(window.localStorage.getItem(ORDER_CODE_KEY) || "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      return "";
    }
    return String(raw[String(taskId)] || "");
  } catch {
    // 隐私模式 / 数据损坏：退化成"本次会话内没有编号"
    return "";
  }
}

/** 记住某任务的订舱编号（提交成功后调用）。 */
export function rememberOrderCode(taskId, orderCode) {
  if (!taskId || !orderCode) {
    return;
  }
  let stored = {};
  try {
    const raw = JSON.parse(window.localStorage.getItem(ORDER_CODE_KEY) || "{}");
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      stored = raw;
    }
  } catch {
    stored = {};
  }
  stored[String(taskId)] = String(orderCode);
  // 与上面同样的上限：对象的键保持插入顺序，超了丢最早的
  const entries = Object.entries(stored).slice(-MAX_REMEMBERED);
  try {
    window.localStorage.setItem(
      ORDER_CODE_KEY,
      JSON.stringify(Object.fromEntries(entries)),
    );
  } catch {
    // 写失败不影响提交流程，只是切任务后看不到编号
  }
}
