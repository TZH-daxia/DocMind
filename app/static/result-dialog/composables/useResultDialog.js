import { fetchRecentFiles, fetchResult, fetchTaskStatus, pageImageUrl } from "../api.js";
import { loadSubmittedTaskIds, rememberSubmittedTask } from "../submittedTasks.js";
import {
  DATE_VALUE_PATTERN,
  FIELD_CONTROLS,
  FIELD_ORDER,
  PARTY_FIELDS,
} from "../constants.js";
import { dialogState } from "../state.js";
import { firstErrorField, validateBeforeSubmit } from "./useSubmitValidation.js";

const loadedTaskIds = new Set();
// 头部页签条固定显示 5 个最近文件（对应设计稿 Frame 84 的 5 个页签：5×216 + 4×4 = 1096px）
const MAX_FILE_TABS = 5;
// 已提交成功的任务 id（localStorage 持久化）：页签条据此显示绿色 + 对号
let submittedTaskIds = loadSubmittedTaskIds();
// 各任务"当前表单值"的内存缓存：切走时存下、切回时恢复，支持来回对照与继续编辑。
// 后端只保存 AI 抽取结果（提交尚未真正落库），所以用户填过的内容必须在前端留住。
const taskForms = new Map();

function rememberForm(taskId) {
  if (!taskId) {
    return;
  }
  taskForms.set(taskId, JSON.parse(JSON.stringify(dialogState.form)));
}

async function loadTask(taskId) {
  if (loadedTaskIds.has(taskId) && dialogState.taskId === taskId) {
    return;
  }
  dialogState.taskId = taskId;
  dialogState.loading = true;
  dialogState.error = "";
  try {
    const status = await fetchTaskStatus(taskId);
    if (dialogState.taskId !== taskId) {
      return;
    }
    dialogState.fileName = status.original_name || "";
    dialogState.taskStatus = status.status || "";
    if (status.status === "failed") {
      dialogState.loading = false;
      dialogState.pageUrls = [];
      dialogState.error = "该任务分析失败，暂无结果可编辑";
      return;
    }
    if (status.status !== "ready" && status.status !== "needs_review") {
      dialogState.loading = false;
      dialogState.pageUrls = [];
      dialogState.error = "结果生成中，完成后将自动填充";
      return;
    }
    const result = await fetchResult(taskId);
    if (dialogState.taskId !== taskId) {
      return;
    }
    applyResult(result, taskId);
    dialogState.pageUrls = buildPageUrls(taskId, status.rendered_page_count);
    dialogState.loading = false;
    loadedTaskIds.add(taskId);
  } catch (error) {
    if (dialogState.taskId !== taskId) {
      return;
    }
    dialogState.loading = false;
    dialogState.error = `结果加载失败：${error.message}`;
  }
}

async function loadFileTabs(activeTaskId) {
  try {
    const payload = await fetchRecentFiles();
    const completed = (payload.items || [])
      // 只显示已解析完成的：后端按"结果文件是否已落盘"给出 result_available
      .filter((item) => item.result_available)
      .map((item) => ({
        taskId: item.task_id,
        name: item.original_name || item.task_id,
        // 已提交成功的文件用绿色 + 对号区分（记录见 submittedTasks.js）
        submitted: submittedTaskIds.has(String(item.task_id)),
      }));
    const tabs = completed.slice(0, MAX_FILE_TABS);
    // 当前文件若不是最近 5 个（例如从左侧列表选了较早的文件），占掉最后一格：
    // 当前项必须能在条上高亮，同时总数仍不超过 MAX_FILE_TABS
    if (!tabs.some((tab) => tab.taskId === activeTaskId)) {
      const current = completed.find((tab) => tab.taskId === activeTaskId);
      if (current) {
        if (tabs.length >= MAX_FILE_TABS) {
          tabs[MAX_FILE_TABS - 1] = current;
        } else {
          tabs.push(current);
        }
      }
    }
    dialogState.fileTabs = tabs;
  } catch {
    // 列表拉不到就退回只显示当前文件名的形态，不阻塞弹窗
    dialogState.fileTabs = [];
  }
}

function buildPageUrls(taskId, pageCount) {
  const total = Number(pageCount) || 0;
  return Array.from({ length: total }, (_, index) => pageImageUrl(taskId, index + 1));
}

function isControlValueValid(control, text) {
  if (control === "date") {
    return DATE_VALUE_PATTERN.test(text);
  }
  if (control === "number" || control === "integer") {
    return Number.isFinite(Number(text));
  }
  return true;
}

function collectLocations(fieldMeta) {
  // 后端按 target（字段 key 或 key.subkey）给出定位框，前端按行 id 直接取用
  const locations = {};
  for (const meta of Object.values(fieldMeta)) {
    for (const item of meta?.locations || []) {
      if (!item?.target) {
        continue;
      }
      locations[item.target] = locations[item.target] || [];
      locations[item.target].push({ page: item.page, bbox: item.bbox });
    }
  }
  return locations;
}

function collectEvidences(fieldMeta) {
  // 待审核字段要在输入框下展示"要审核的原文"，这里把后端给的证据引用按字段收好
  const evidences = {};
  for (const [key, meta] of Object.entries(fieldMeta || {})) {
    const quotes = (meta?.evidence || [])
      .map((item) => String(item?.quote || "").trim())
      .filter(Boolean);
    if (quotes.length) {
      evidences[key] = quotes;
    }
  }
  return evidences;
}

function collectPortCandidates(fieldMeta) {
  // 港口转三字码失败时后端会置空字段并把候选一起下发，前端在空字段下方展示
  const candidates = {};
  for (const [key, meta] of Object.entries(fieldMeta || {})) {
    const items = meta?.candidates || [];
    if (!items.length) continue;
    candidates[key] = items.map((item) => ({
      three_code: item.three_code || "",
      english_name: item.english_name || "",
      country_code: item.country_code || "",
    }));
  }
  return candidates;
}

function applyResult(result, taskId) {
  const values = result.result || {};
  const meta = result.field_meta || {};
  const form = {};
  const original = {};
  const fieldStatus = {};
  const rawValues = {};
  for (const key of FIELD_ORDER) {
    const value = values[key];
    const control = FIELD_CONTROLS[key] || "text";
    const status = meta[key]?.status;
    fieldStatus[key] = status || (value == null ? "missing" : "confirmed");
    if (control === "party") {
      const party = value && typeof value === "object" ? value : {};
      const group = {};
      for (const item of PARTY_FIELDS) {
        group[item.key] = party[item.key] ?? "";
      }
      form[key] = group;
    } else {
      const text = value == null ? "" : String(value);
      // 控件本身渲染不出来的原值（日期区间、带单位的数字等）一律不进入校验：
      // 界面显示为空就必须按空拦截，原值移到 rawValues 作为提示交给人工确认，
      // 避免出现"界面看着没填、校验却认为有值"的漏放
      if (text && !isControlValueValid(control, text)) {
        rawValues[key] = text;
        form[key] = "";
      } else {
        form[key] = text;
      }
    }
    original[key] = JSON.parse(JSON.stringify(form[key]));
  }
  // 该任务之前填过：用缓存覆盖表单值。original 仍是 AI 抽取结果，
  // 所以"已修改"标记依然能正确指出哪些字段被人改过
  const cachedForm = taskForms.get(taskId);
  if (cachedForm) {
    for (const key of FIELD_ORDER) {
      if (key in cachedForm) {
        form[key] = JSON.parse(JSON.stringify(cachedForm[key]));
      }
    }
  }
  dialogState.form = form;
  dialogState.original = original;
  dialogState.fieldStatus = fieldStatus;
  dialogState.rawValues = rawValues;
  dialogState.evidences = collectEvidences(meta);
  dialogState.locations = collectLocations(meta);
  dialogState.portCandidates = collectPortCandidates(meta);
  dialogState.focusedLocationKey = "";
  dialogState.highlightBoxes = [];
  dialogState.highlightStatus = "";
  dialogState.errors = {};
  dialogState.submitting = false;
  dialogState.error = "";
}

export const resultDialog = {
  async open(taskId) {
    if (!taskId) {
      return;
    }
    dialogState.visible = true;
    // 从主页面选了另一个文件打开弹窗时，先把上一个任务填过的内容存下来
    if (dialogState.taskId && dialogState.taskId !== taskId) {
      rememberForm(dialogState.taskId);
    }
    // 页签条与当前任务并行加载，避免串行等待
    await Promise.all([loadFileTabs(taskId), loadTask(taskId)]);
  },

  async switchTask(taskId) {
    if (!taskId || taskId === dialogState.taskId) {
      return;
    }
    // 切换不再丢改动：先把当前任务填过的内容存起来，切回来会原样恢复，
    // 因此不再弹"将丢弃"确认框，来回对照两个文件也不会被打断
    rememberForm(dialogState.taskId);
    await loadTask(taskId);
  },

  // 提交成功后标记该文件：页签条立刻变绿（并持久化，刷新后仍在）
  markSubmitted(taskId) {
    if (!taskId) {
      return;
    }
    submittedTaskIds = rememberSubmittedTask(taskId);
    dialogState.fileTabs = dialogState.fileTabs.map((tab) =>
      tab.taskId === taskId ? { ...tab, submitted: true } : tab,
    );
    // 提交成功后把当前内容也存一份：切走再切回来仍是提交时的样子
    rememberForm(taskId);
  },

  close() {
    dialogState.visible = false;
    dialogState.errors = {};
  },

  focusField({ locationKey, fieldKey, status, hasValue }) {
    // 聚焦即高亮：优先用该行的定位框，退化到所属字段的框；都没有则清空
    if (hasValue === false) {
      // 空值行不定位：清掉上一处高亮，避免误导成"这个空字段来自原文该处"
      dialogState.focusedLocationKey = "";
      dialogState.highlightStatus = "";
      dialogState.highlightBoxes = [];
      return;
    }
    dialogState.focusedLocationKey = locationKey || "";
    dialogState.highlightStatus = status || "";
    const own = locationKey ? dialogState.locations[locationKey] : null;
    const fallback = fieldKey ? dialogState.locations[fieldKey] : null;
    dialogState.highlightBoxes = own?.length ? own : fallback || [];
  },

  submit() {
    // 只做本地必填校验，不关闭弹窗：远程校验通过后才收起（见 onSubmit）
    const errors = validateBeforeSubmit(dialogState.form);
    dialogState.errors = errors;
    if (Object.keys(errors).length) {
      return { ok: false, errors, firstError: firstErrorField(errors) };
    }
    return { ok: true, errors: {}, firstError: null };
  },
};
