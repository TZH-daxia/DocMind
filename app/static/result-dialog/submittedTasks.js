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
