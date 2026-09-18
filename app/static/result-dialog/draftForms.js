/**
 * 各任务"当前表单值"的本地草稿：切走/切回、刷新页面后都能恢复人工填过的内容。
 *
 * 为什么必须持久化：后端只保存 AI 抽取结果，真实提交接口尚未接入（提交目前是前端
 * 假设性的），没有任何地方能查回"用户填过的值"。此前草稿只存在内存 Map 里，刷新即丢，
 * 而"已提交"标记在 localStorage 里还在 —— 于是任务会变成
 * 「显示已提交 + 表单锁定不可编辑 + 内容是 AI 原值」的死局，人工核对成果全丢。
 *
 * 等提交接口落地后，这里应改为从后端读草稿/已提交内容，前端只做短时缓存。
 */

import { loadSubmittedTaskIds } from "./submittedTasks.js";

const STORAGE_KEY = "docmind.draftForms";
// 保留条数：与 submittedTasks 同量级。淘汰时优先丢"未提交"的草稿，
// 已提交任务的草稿必须留着，否则又会出现上面那种不一致状态
const MAX_REMEMBERED = 200;
// 单份草稿体积上限（字符数）：异常大的表单不写入，避免挤爆 localStorage 配额
const MAX_DRAFT_CHARS = 64 * 1024;

/** 读取某任务的草稿表单；没有则返回 null。 */
export function loadDraftForm(taskId) {
  if (!taskId) {
    return null;
  }
  const entry = readItems()[String(taskId)];
  return entry && entry.form && typeof entry.form === "object" ? entry.form : null;
}

/** 记下某任务的表单值（写入前做深拷贝，避免后续编辑改到存档）。 */
export function rememberDraftForm(taskId, form) {
  if (!taskId || !form) {
    return;
  }
  const payload = JSON.stringify(form);
  if (payload.length > MAX_DRAFT_CHARS) {
    return;
  }
  const items = readItems();
  items[String(taskId)] = { savedAt: Date.now(), form: JSON.parse(payload) };
  writeItems(trim(items));
}

function readItems() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    return raw && raw.items && typeof raw.items === "object" ? raw.items : {};
  } catch {
    // 隐私模式 / 数据损坏：退化成"本次会话内有效"
    return {};
  }
}

function writeItems(items) {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: 1, items }),
    );
  } catch {
    // 配额满或不可写：不影响提交流程，只是刷新后丢草稿
  }
}

function trim(items) {
  const entries = Object.entries(items);
  if (entries.length <= MAX_REMEMBERED) {
    return items;
  }
  const submitted = loadSubmittedTaskIds();
  const rank = ([taskId, entry]) =>
    [submitted.has(taskId) ? 1 : 0, entry.savedAt || 0];
  entries.sort((a, b) => {
    const [keepA, timeA] = rank(a);
    const [keepB, timeB] = rank(b);
    return keepA - keepB || timeA - timeB;
  });
  return Object.fromEntries(entries.slice(entries.length - MAX_REMEMBERED));
}
