import {
  fetchProjects,
  fetchRecentFiles,
  fetchResult,
  fetchSites,
  fetchTaskStatus,
  fetchUserDefaults,
  pageImageUrl,
  submitOrder,
} from "../api.js";
import { currentUserDom, currentUserName } from "../currentUser.js";
import {
  loadDraftForm,
  loadDraftOrder,
  loadDraftServices,
  mergeDraftForm,
  rememberDraftForm,
  rememberDraftOrder,
  rememberDraftServices,
} from "../draftForms.js";
import {
  mergeOrderDelta,
  orderDiffersFromBase,
  resolveOrderDefaults,
} from "../orderDefaults.js";
import { DEFAULT_SERVICE_CODES, normalizeServiceCodes } from "../serviceItems.js";
import {
  loadOrderCode,
  loadSubmittedTaskIds,
  rememberOrderCode,
  rememberSubmittedTask,
} from "../submittedTasks.js";
import {
  clearSubmitRequestId,
  loadSubmitRequestId,
  newSubmitRequestId,
  rememberSubmitRequestId,
} from "../submitRequestId.js";
import {
  CONTEXT_FIELD_DEFAULTS,
  CONTEXT_FIELDS,
  DATE_VALUE_PATTERN,
  FIELD_CONTROLS,
  FIELD_ORDER,
  PARTY_FIELDS,
} from "../constants.js";
import { dialogState } from "../state.js";
import { isValidNumericField } from "../fields.js";
import { firstErrorField, validateBeforeSubmit } from "./useSubmitValidation.js";

const { watch } = window.Vue;

const loadedTaskIds = new Set();
// 头部页签条固定显示 5 个最近文件（对应设计稿 Frame 84 的 5 个页签：5×216 + 4×4 = 1096px）
const MAX_FILE_TABS = 5;
// 已提交成功的任务 id（localStorage 持久化）：页签条据此显示绿色 + 对号
let submittedTaskIds = loadSubmittedTaskIds();

// 记住某任务填过的内容：草稿落在 localStorage（见 draftForms.js），
// 因此切走再切回、甚至刷新页面后都能恢复，人工核对成果不会丢。
// 表单与订单上下文（工具条四项）都要存：后者全局只有一份，漏存就会出现
//「切任务 / 刷新后，工具条显示的不是这个任务填过的值」
function rememberForm(taskId) {
  if (!taskId) {
    return;
  }
  rememberDraftForm(taskId, dialogState.form);
  // 订单上下文一并存，并把"默认值基线"带上：重开时据此只沿用人工改过的字段
  //（见 orderDefaults.js 的 mergeOrderDelta）
  rememberDraftOrder(taskId, dialogState.order, orderBase);
  // 服务项目落盘前先归一：带上配舱服务、按面板顺序，勾选顺序（点击顺序）不落盘
  rememberDraftServices(taskId, normalizeServiceCodes(dialogState.serviceCodes));
}

// 输入即存（防抖 400ms）：填到一半就刷新/关标签页也不会丢。
// 切换任务与提交成功时另有立即写入，所以这里只兜"边填边存"这一种情况。
const DRAFT_DEBOUNCE_MS = 400;
let draftTimer = null;
// `dialogState.form` 当前**属于哪个任务**（null = 还没有归属，正在切换中）。
// 只比对 dialogState.taskId 是不够的：loadTask 一进去就把 taskId 换成了新任务，而
// form 要到 applyResult 才替换。这中间行组件按 resetKey 重建，ProjectSelect /
// ContactSelect 会拿**上一版的 form** 自动回写（自动带出唯一项目 / 默认联系人），
// 那些内容就会被当成"新任务的草稿"存下来 —— 表现为切换任务后表单内容串到另一个任务
let formTaskId = null;
watch(
  () => dialogState.form,
  () => {
    const taskId = dialogState.taskId;
    if (!taskId || formTaskId !== taskId) {
      return;
    }
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      // 表单变化后任务已切走（或表单已不再属于这个任务）：不要再落到这个任务名下
      if (dialogState.taskId === taskId && formTaskId === taskId) {
        rememberDraftForm(taskId, dialogState.form);
      }
    }, DRAFT_DEBOUNCE_MS);
  },
  { deep: true },
);

// 工具条上的订单上下文（站点 / 服务方式 / 运输种类 / 订舱操作）同样按任务存，
// 但**只存人工改过的**：与"打开任务时算出的默认值基线"一致时说明没人动过，
// 不落草稿 —— 这样没被改过的任务始终跟随最新的用户默认设置（在 poOrder 里改了默认
// 站点，重开任务就能看到），被人改过的任务才保留自己那份，互不影响。
let orderDraftTimer = null;
watch(
  () => dialogState.order,
  () => {
    const taskId = dialogState.taskId;
    if (!taskId || !orderDiffersFromBase(dialogState.order, orderBase)) {
      return;
    }
    clearTimeout(orderDraftTimer);
    orderDraftTimer = setTimeout(() => {
      if (dialogState.taskId === taskId && orderDiffersFromBase(dialogState.order, orderBase)) {
        rememberDraftOrder(taskId, dialogState.order, orderBase);
      }
    }, DRAFT_DEBOUNCE_MS);
  },
  { deep: true },
);

// 服务项目（勾选的服务代码）同样按任务存：勾一次落一次，切任务 / 刷新后还原
let servicesDraftTimer = null;
watch(
  () => dialogState.serviceCodes,
  () => {
    const taskId = dialogState.taskId;
    if (!taskId) {
      return;
    }
    clearTimeout(servicesDraftTimer);
    servicesDraftTimer = setTimeout(() => {
      if (dialogState.taskId === taskId) {
        rememberDraftServices(taskId, orderServiceCodes(dialogState.serviceCodes));
      }
    }, DRAFT_DEBOUNCE_MS);
  },
  { deep: true },
);

// 当前任务的"默认值基线"（工具条四项按「上传 context > 用户默认设置 > 内置兜底」算出来的值）：
// 用来判断工具条有没有被人改过 —— 改过才落草稿。null = 还没算出来（此期间不落草稿）
let orderBase = null;

// 用户默认设置按登录名**短时**缓存。不能缓存整个页面会话：在 poOrder 里改了默认设置后，
// 页面开着一整天就会一直用旧值（表现为"有时生效有时无效"）。60 秒足够挡掉连续切任务的
// 重复请求，又能让改动很快生效（后端另有同量级的缓存）
const USER_DEFAULTS_TTL_MS = 60 * 1000;
let userDefaultsCache = null;

async function loadUserDefaults(logname) {
  const name = String(logname || "").trim();
  if (!name) {
    // 没有登录名就查不到默认设置（接口按 logname 索引），直接走内置兜底
    return null;
  }
  const cached = userDefaultsCache;
  if (cached && cached.logname === name && Date.now() - cached.at < USER_DEFAULTS_TTL_MS) {
    return cached.promise;
  }
  const promise = fetchUserDefaults(name).catch(() => {
    // 拉不到就当"该用户没配默认设置"：工具条退到内置兜底，不打断开单；
    // 同时清掉缓存，让下一次开任务立刻重试
    if (userDefaultsCache?.logname === name) {
      userDefaultsCache = null;
    }
    return null;
  });
  userDefaultsCache = { logname: name, at: Date.now(), promise };
  return promise;
}

/**
 * 算出当前任务的**默认值基线**并铺进工具条。
 *
 * 每次打开任务都重算（用户可能刚在 poOrder 里改了默认设置），算完只存进 orderBase，
 * **不落草稿** —— 没被人动过的任务因此始终跟随最新默认值；被人工改过的字段由
 * mergeOrderDelta 叠在基线之上（见 loadTask）。
 */
async function applyOrderDefaults(taskId, context) {
  const userDefaults = await loadUserDefaults(currentUserName());
  if (dialogState.taskId !== taskId) {
    return;
  }
  orderBase = resolveOrderDefaults({ context: context || {}, userDefaults });
  for (const [key, value] of Object.entries(orderBase)) {
    dialogState.order[key] = value;
  }
}

/** 把该任务**人工改过**的工具条字段叠到刚算出的基线上（改过的沿用，没改的跟随基线）。 */
function applySavedOrderDelta(savedOrder) {
  if (!savedOrder) {
    return;
  }
  const merged = mergeOrderDelta({
    base: orderBase,
    savedOrder: savedOrder.order,
    savedBase: savedOrder.base,
  });
  for (const [key, value] of Object.entries(merged)) {
    dialogState.order[key] = value;
  }
}

async function loadTask(taskId) {
  if (loadedTaskIds.has(taskId) && dialogState.taskId === taskId) {
    return;
  }
  dialogState.taskId = taskId;
  // 立刻清掉上一个任务的展示内容。form / 原文定位 / 证据 / 错误标记都还挂着上一个
  // 任务的值，而本任务的结果要等两个网络往返才回来：不清的话，这段时间里行组件
  // 重建后会读到上一个任务的表单（看起来"内容串到新任务了"），自组件还会据此回写；
  // formTaskId 置空则让草稿 watcher 在这一窗口内闭嘴（见它的说明）
  formTaskId = null;
  dialogState.form = {};
  // 原件图片也一并清掉：否则切换期间左侧还挂着上一个任务的页面图
  dialogState.pageUrls = [];
  dialogState.original = {};
  dialogState.rawValues = {};
  dialogState.evidences = {};
  dialogState.locations = {};
  dialogState.portCandidates = {};
  dialogState.focusedLocationKey = "";
  dialogState.highlightBoxes = [];
  dialogState.highlightStatus = "";
  dialogState.fieldStatus = {};
  dialogState.fieldSelections = {};
  dialogState.errors = {};
  // 已提交状态跟着任务走：刷新页面后也从本地记录恢复，按钮仍是「已提交」
  dialogState.submitted = submittedTaskIds.has(String(taskId));
  // 订舱编号同样跟着任务走：切任务、刷新页面后仍显示（在弹窗头部的编号位上）；
  // 该任务没提交过就置空，避免把上一个任务的编号带过来
  dialogState.orderCode = loadOrderCode(taskId);
  // 工具条（站点 / 服务方式 / 运输种类 / 订舱操作）分两步处理，都在状态接口回来之后：
  // ① 按「上传 context > 用户默认设置 > 内置兜底」算出**基线**（每次打开都重算，
  //    所以在 poOrder 里改了默认设置后，没被人动过的任务重开就能跟上）；
  // ② 把这个任务**人工改过**的字段叠上去（草稿的差分，见 mergeOrderDelta）。
  // 注意顺序不能反，且基线算出来之前不落草稿（见上方 watcher）
  const savedOrder = loadDraftOrder(taskId);
  // 立刻清掉上一个任务的基线：在算出本任务的基线前，任何变化都不该被当成"人工改动"
  // 存下来（否则会把上一个任务的工具条值钉进这个任务）
  orderBase = null;
  // 服务项目（勾选的服务代码）也按任务恢复；没存过的任务回到默认勾选（唯凯配舱）
  const savedServices = loadDraftServices(taskId);
  dialogState.serviceCodes = savedServices
    ? normalizeServiceCodes(savedServices)
    : [...DEFAULT_SERVICE_CODES];
  if (!savedServices) {
    rememberDraftServices(taskId, dialogState.serviceCodes);
  }
  // 遮罩跟着实际加载时长走，不做人为延时：没改动过的任务往往秒开，那就一闪而过
  dialogState.loading = true;
  dialogState.error = "";
  try {
    const status = await fetchTaskStatus(taskId);
    if (dialogState.taskId !== taskId) {
      return;
    }
    // ① 基线（含一次「用户默认设置」请求，结果按登录名短时缓存）；上传 context 就在
    //    status.context 里，所以必须放在状态接口之后
    await applyOrderDefaults(taskId, status.context);
    if (dialogState.taskId !== taskId) {
      return;
    }
    // ② 叠加这个任务人工改过的字段（没有草稿就是纯基线）
    applySavedOrderDelta(savedOrder);
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

function isControlValueValid(key, control, text) {
  if (control === "date") {
    return DATE_VALUE_PATTERN.test(text);
  }
  if (control === "number" || control === "integer") {
    // 与提交校验同一套规则：0 / 负数 / 科学计数 / 小数当整数 一律判非法。
    // 于是 AI 给出的这类值会被挪进 rawValues 提示人工确认，而不是原样进表单
    //（原实现只要 Number.isFinite 就放行，负值、0 与 1e3 都能进表单）
    return isValidNumericField(key, text);
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
      if (text && !isControlValueValid(key, control, text)) {
        rawValues[key] = text;
        form[key] = "";
      } else {
        form[key] = text;
      }
    }
    original[key] = JSON.parse(JSON.stringify(form[key]));
  }
  // 横栏字段（项目 gid/wtxmname/wtxmcode、本票客服联系人 customerRelList）：它们不在
  // 表格里，不参与行渲染与"已修改"标记，但必须给一个确定的初值 —— 否则表单重建后
  // 这些键直接不存在（手输/清空会表现为 undefined），下面 restore 也才有落脚点
  for (const key of CONTEXT_FIELDS) {
    form[key] = JSON.parse(JSON.stringify(CONTEXT_FIELD_DEFAULTS[key] ?? ""));
  }
  // 该任务之前填过（含刷新后的本地草稿）：用它覆盖表单值。original 仍是 AI 抽取结果，
  // 所以"已修改"标记依然能正确指出哪些字段被人改过。
  // 恢复范围含横栏字段 —— 只恢复表格字段会让"人工选好的项目"在切任务/刷新后消失
  mergeDraftForm(form, loadDraftForm(taskId));
  dialogState.form = form;
  // 到这里 form 才真正属于这个任务：允许草稿 watcher 开始记账（见 formTaskId 的说明）
  formTaskId = taskId;
  dialogState.original = original;
  dialogState.fieldStatus = fieldStatus;
  dialogState.rawValues = rawValues;
  // 组合框确认状态（fieldSelections）已在 loadTask 开头清空，行组件按 resetKey 重建后
  // mounted 会重新上报；同一个任务重新加载结果时组件不重建，但状态本来也没变
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
    const errors = validateBeforeSubmit(dialogState.form, dialogState.fieldSelections);
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
    // 站点（area）是报文必填项，信控、项目站点权限也都按它判定；为空时不要发出去
    //（poOrder 前端拦截器对这种情况的提示就是「请选择区域」）
    if (!String(dialogState.order.area || "").trim()) {
      return {
        ok: false,
        errors: {},
        firstError: null,
        message: "请选择区域",
      };
    }
    if (dialogState.submitting) {
      // 上一次还没回来：避免连点造成重复下单
      return { ok: false, errors: {}, firstError: null, busy: true };
    }
    // 幂等键：同一把键的重试不会被后端当成新单。按任务持久化，刷新页面后重试仍是
    // 同一次尝试；只有后端确认"已有定论"（retryable）时才作废、下次换新键。
    const requestId =
      loadSubmitRequestId(dialogState.taskId) ||
      rememberSubmitRequestId(dialogState.taskId, newSubmitRequestId());
    dialogState.submitting = true;
    try {
      const outcome = await submitOrder({
        form: dialogState.form,
        // 订单上下文给副本：后端只读，避免把响应字段写回响应式对象。
        // dom（部门）也在这里带上：poOrder 的列表查询默认按部门过滤，缺了会让新单
        // 在综合查询里查不到；开发期用 ?dom= 传，缺省由后端回落「出口部」
        order: { ...dialogState.order, dom: currentUserDom() },
        // 服务项目：已勾选的服务代码（含配舱服务、按面板顺序），后端据此生成 serviceList
        service_codes: normalizeServiceCodes(dialogState.serviceCodes),
        // 当前用户（登录名）= 报文的 czman 与 customerRelList[].addman。生产环境由官网
        // 传入，开发期由 currentUser.js 从 URL 参数 / poOrder 的 Cookie 兜底
        czman: currentUserName(),
        // 幂等键：后端据此回放首次结果、不重复下单
        request_id: requestId,
        // poOrder 票据不放这里：由 api.js 的 ticketHeaders() 统一放进 Authorization
        // 请求头（不进 URL、不进请求体），后端 current_ticket 依赖同口径读取
      });
      if (!outcome.ok) {
        // 后端说"已有定论"（可安全重试）→ 作废幂等键，下次点击算全新一单；
        // 结果未知（超时 / 正在提交中）→ 保留键，重试仍打在同一把键上，不会重复建单。
        // 老版本后端不返回该字段：按"可重试"处理，退回改动前的行为
        if (outcome.retryable !== false) {
          clearSubmitRequestId(dialogState.taskId);
        }
        // 接口给出的原因原样带出去（缺操作人 / 客户信控 / 后端异常）
        return {
          ok: false,
          errors: {},
          firstError: null,
          message: outcome.message || "提交失败",
        };
      }
      // 成功：这票订单已定论，清掉幂等键
      clearSubmitRequestId(dialogState.taskId);
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
        // true = 这次是幂等回放（重试命中了上一次的结果），提示文案据此换措辞
        duplicated: Boolean(outcome.duplicated),
        message: outcome.message || "",
      };
    } catch (error) {
      // 连响应都没拿到（超时 / 断网）：结果未知，**保留**幂等键，
      // 用户再点「提交」就是同一次尝试的重试，后端回放结果、不会重复建单
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
