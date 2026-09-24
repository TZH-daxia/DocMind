import {
  fetchProjects,
  fetchRecentFiles,
  fetchResult,
  fetchSites,
  fetchTaskStatus,
  pageImageUrl,
  submitOrder,
} from "../api.js";
import { currentTicket, currentUserDom, currentUserName } from "../currentUser.js";
import { loadDraftForm, rememberDraftForm } from "../draftForms.js";
import {
  loadOrderCode,
  loadSubmittedTaskIds,
  rememberOrderCode,
  rememberSubmittedTask,
} from "../submittedTasks.js";
import {
  DATE_VALUE_PATTERN,
  FIELD_CONTROLS,
  FIELD_ORDER,
  PARTY_FIELDS,
} from "../constants.js";
import { dialogState } from "../state.js";
import { firstErrorField, validateBeforeSubmit } from "./useSubmitValidation.js";

const { watch } = window.Vue;

const loadedTaskIds = new Set();
// 头部页签条固定显示 5 个最近文件（对应设计稿 Frame 84 的 5 个页签：5×216 + 4×4 = 1096px）
const MAX_FILE_TABS = 5;
// 已提交成功的任务 id（localStorage 持久化）：页签条据此显示绿色 + 对号
let submittedTaskIds = loadSubmittedTaskIds();

// 记住某任务填过的内容：草稿落在 localStorage（见 draftForms.js），
// 因此切走再切回、甚至刷新页面后都能恢复，人工核对成果不会丢
function rememberForm(taskId) {
  if (!taskId) {
    return;
  }
  rememberDraftForm(taskId, dialogState.form);
}

// 输入即存（防抖 400ms）：填到一半就刷新/关标签页也不会丢。
// 切换任务与提交成功时另有立即写入，所以这里只兜"边填边存"这一种情况。
const DRAFT_DEBOUNCE_MS = 400;
let draftTimer = null;
watch(
  () => dialogState.form,
  () => {
    const taskId = dialogState.taskId;
    if (!taskId) {
      return;
    }
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      // 表单变化后任务已切走：不要再把上一个任务的表单落到新任务名下
      if (dialogState.taskId === taskId) {
        rememberDraftForm(taskId, dialogState.form);
      }
    }, DRAFT_DEBOUNCE_MS);
  },
  { deep: true },
);

async function loadTask(taskId) {
  if (loadedTaskIds.has(taskId) && dialogState.taskId === taskId) {
    return;
  }
  dialogState.taskId = taskId;
  // 已提交状态跟着任务走：刷新页面后也从本地记录恢复，按钮仍是「已提交」
  dialogState.submitted = submittedTaskIds.has(String(taskId));
  // 订舱编号同样跟着任务走：切任务、刷新页面后仍显示（在弹窗头部的编号位上）；
  // 该任务没提交过就置空，避免把上一个任务的编号带过来
  dialogState.orderCode = loadOrderCode(taskId);
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
  // 该任务之前填过（含刷新后的本地草稿）：用它覆盖表单值。original 仍是 AI 抽取结果，
  // 所以"已修改"标记依然能正确指出哪些字段被人改过
  const cachedForm = loadDraftForm(taskId);
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

// 站点字典候选：全局参考数据、与任务无关，拉一次即可（每次打开弹窗都会调用，
// 用 loaded 标志避免重复请求）。失败不阻断弹窗：静默留空，胶囊仍显示订单上下文
// 带来的站点原值
async function loadSiteGroups() {
  if (dialogState.siteGroupsLoaded) {
    return;
  }
  dialogState.siteGroupsLoaded = true;
  try {
    const payload = await fetchSites();
    dialogState.siteGroups = payload.groups || [];
  } catch {
    dialogState.siteGroups = [];
  }
}

export const resultDialog = {
  async open(taskId) {
    if (!taskId) {
      return;
    }
    dialogState.visible = true;
    // 站点候选与任务无关，不等它、不阻塞弹窗打开
    loadSiteGroups();
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
    // 正打开的就是这个任务：按钮立刻变「已提交」并置灰，无需重新加载
    if (dialogState.taskId === taskId) {
      dialogState.submitted = true;
    }
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

  async submit() {
    // 先做本地必填校验（必填项在 constants.js 的 REQUIRED_FIELDS），再提交给后端。
    // 本地不通过就直接返回，连接口都不打
    const errors = validateBeforeSubmit(dialogState.form);
    dialogState.errors = errors;
    if (Object.keys(errors).length) {
      return { ok: false, errors, firstError: firstErrorField(errors) };
    }
    // 订舱操作要进报文的 czlx，不能为空：工具条默认已给「自货」，这里再兜一道，
    // 避免重置/异常情况下空值漏给接口
    if (!String(dialogState.order.czlx || "").trim()) {
      return {
        ok: false,
        errors: {},
        firstError: null,
        message: "请先选择「订舱操作」",
      };
    }
    // 「项目」(gid) 是按客户条件必填的：该委托客户下**有**可选项目时必须选一个；
    // 一个都没有（有些客户没建项目）则留空即可——所以它不能放进 REQUIRED_FIELDS，
    // 只能在这里按客户实际有无候选项判断
    if (!String(dialogState.form.gid || "").trim()) {
      const customerId = String(dialogState.form.fid || "").trim();
      if (/^\d+$/.test(customerId)) {
        try {
          const projectPayload = await fetchProjects(customerId);
          if ((projectPayload.items || []).length) {
            dialogState.errors = { ...dialogState.errors, gid: true };
            return {
              ok: false,
              errors: dialogState.errors,
              firstError: "gid",
              message: "该委托客户有可选项目，请先选择「项目」",
            };
          }
        } catch {
          // 项目主数据拿不到：不在这里拦，留给后端与人工核对
        }
      }
    }
    if (dialogState.submitting) {
      // 上一次还没回来：避免连点造成重复下单
      return { ok: false, errors: {}, firstError: null, busy: true };
    }
    dialogState.submitting = true;
    try {
      const outcome = await submitOrder({
        form: dialogState.form,
        // 订单上下文给副本：后端只读，避免把响应字段写回响应式对象。
        // dom（部门）也在这里带上：poOrder 的列表查询默认按部门过滤，缺了会让新单
        // 在综合查询里查不到；开发期用 ?dom= 传，缺省由后端回落「出口部」
        order: { ...dialogState.order, dom: currentUserDom() },
        // 当前用户（登录名）= 报文的 czman 与 customerRelList[].addman。生产环境由官网
        // 传入，开发期由 currentUser.js 从 URL 参数 / poOrder 的 Cookie 兜底
        czman: currentUserName(),
        ticket: currentTicket(),
      });
      if (!outcome.ok) {
        // 接口给出的原因原样带出去（缺操作人 / 客户信控 / 后端异常）
        return {
          ok: false,
          errors: {},
          firstError: null,
          message: outcome.message || "提交失败",
        };
      }
      // 订舱编号显示在弹窗头部、叉号左侧（state.orderCode）。
      // 以后端返回的 order_code 为准；万一后端那层没取到（例如服务还没重启、
      // 或 poOrder 又换了措辞），再按编号格式从接口提示里兜一次底：
      // 形如 BOAE2609240001PVG（前缀 + 日期 + 流水 + 始发港）
      const orderCode =
        outcome.order_code ||
        // poOrder 的原始响应里编号在 resultno；万一后端那层没读到，这里也能兜住
        String(outcome.response?.resultno || "") ||
        (/[A-Z]{2,}[0-9]{6,}[A-Z]*/.exec(String(outcome.message || "")) ||
          [""])[0];
      if (orderCode) {
        dialogState.orderCode = orderCode;
        // 落本地：切到别的任务再切回来、甚至刷新页面，编号仍在（按任务存）
        rememberOrderCode(dialogState.taskId, orderCode);
      }
      // 标记已提交：页签变绿 + 按钮置灰（含 localStorage 持久化，刷新后仍在）
      resultDialog.markSubmitted(dialogState.taskId);
      return {
        ok: true,
        errors: {},
        firstError: null,
        // 用兜底后的编号：提示文案与编号槽显示的是同一个值
        orderCode,
        message: outcome.message || "",
      };
    } catch (error) {
      return {
        ok: false,
        errors: {},
        firstError: null,
        message: error?.message || "提交接口调用失败，请稍后重试",
      };
    } finally {
      dialogState.submitting = false;
    }
  },
};
