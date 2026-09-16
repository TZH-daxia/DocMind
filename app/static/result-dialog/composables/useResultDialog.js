import { fetchResult, fetchTaskStatus, pageImageUrl } from "../api.js";
import {
  DATE_VALUE_PATTERN,
  FIELD_CONTROLS,
  FIELD_ORDER,
  PARTY_FIELDS,
} from "../constants.js";
import { dialogState } from "../state.js";
import { firstErrorField, validateBeforeSubmit } from "./useSubmitValidation.js";

const loadedTaskIds = new Set();

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
    applyResult(result);
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

function applyResult(result) {
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
  dialogState.form = form;
  dialogState.original = original;
  dialogState.fieldStatus = fieldStatus;
  dialogState.rawValues = rawValues;
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
    await loadTask(taskId);
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
