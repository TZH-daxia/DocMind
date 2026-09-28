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

import { CONTEXT_FIELDS, FIELD_ORDER } from "./constants.js";
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

/**
 * 读取某任务保存过的订单上下文（工具条上的站点 / 服务方式 / 运输种类 / 订舱操作）。
 *
 * 它跟表单不是一回事，但同样属于「人工核对过的内容」：值按上传来源来（不同文件可能
 * 来自不同站点、不同业务），在弹窗里也能被人工改。它只存在全局的 `dialogState.order`
 * 上 —— 不按任务存的话，切任务时会被上一个任务的值顶掉、刷新后又会退回硬编码默认值
 *（上海 / 空运 / 出口），于是「之前填的和现在显示的不一样」。
 */
export function loadDraftOrder(taskId) {
  if (!taskId) {
    return null;
  }
  const entry = readItems()[String(taskId)];
  if (!entry || !entry.order || typeof entry.order !== "object") {
    return null;
  }
  // base = 保存这份快照时算出的"默认值基线"：调用方据此判断哪些字段是人工改过的
  //（与基线相同即没人动过，重开时应跟随最新的默认设置而不是钉在旧值上）。
  // 早期版本的记录没有 base，读出来是 null，调用方按"整体沿用"处理
  return {
    order: entry.order,
    base: entry.orderBase && typeof entry.orderBase === "object" ? entry.orderBase : null,
  };
}

/**
 * 读取某任务勾选过的服务项目（服务代码数组）；没有则返回 null。
 *
 * 与表单 / 订单上下文同样是「人工核对过的内容」：勾选状态进提交报文的 serviceList，
 * 切任务或刷新后必须还原，否则操作员会以为没勾过、或重复勾选。
 */
export function loadDraftServices(taskId) {
  if (!taskId) {
    return null;
  }
  const entry = readItems()[String(taskId)];
  return Array.isArray(entry?.services) ? entry.services : null;
}

/**
 * 把草稿里的值合并回表单。
 *
 * 恢复范围是「表格字段（FIELD_ORDER）+ 横栏字段（CONTEXT_FIELDS）」：前者是表单行，
 * 后者是 ProjectSelect / ContactSelect 写进 form 的项目（gid / wtxmname / wtxmcode）
 * 与本票客服联系人（customerRelList）。两边都必须恢复 —— 只恢复表格字段的话，
 * 切任务/刷新后人工选好的项目会显示不出来（草稿里其实存着）。
 */
export function mergeDraftForm(form, cachedForm) {
  if (!form || !cachedForm) {
    return form;
  }
  for (const key of [...FIELD_ORDER, ...CONTEXT_FIELDS]) {
    if (key in cachedForm) {
      form[key] = JSON.parse(JSON.stringify(cachedForm[key]));
    }
  }
  return form;
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
  // 同一条记录里还存着订单上下文（order）与服务项目（services），不能整条覆盖掉
  items[String(taskId)] = {
    savedAt: Date.now(),
    form: JSON.parse(payload),
    order: items[String(taskId)]?.order,
    services: items[String(taskId)]?.services,
  };
  writeItems(trim(items));
}

/**
 * 记下某任务的订单上下文（工具条四项；与表单共用同一条记录）。
 *
 * `base` 是这次保存时算出的**默认值基线**（`orderDefaults.js` 的 resolveOrderDefaults）。
 * 存它的意义：重开任务时只沿用"与基线不同"的字段（= 人工改过的），其余跟随最新默认值，
 * 这样改了 poOrder 的默认站点后，没被人动过的任务能立刻跟上。
 */
export function rememberDraftOrder(taskId, order, base) {
  if (!taskId || !order) {
    return;
  }
  const items = readItems();
  items[String(taskId)] = {
    savedAt: Date.now(),
    form: items[String(taskId)]?.form,
    order: JSON.parse(JSON.stringify(order)),
    orderBase: base ? JSON.parse(JSON.stringify(base)) : null,
    services: items[String(taskId)]?.services,
  };
  writeItems(trim(items));
}

/** 记下某任务勾选的服务项目（服务代码数组；与表单共用同一条记录）。 */
export function rememberDraftServices(taskId, codes) {
  if (!taskId || !Array.isArray(codes)) {
    return;
  }
  const items = readItems();
  items[String(taskId)] = {
    savedAt: Date.now(),
    form: items[String(taskId)]?.form,
    order: items[String(taskId)]?.order,
    services: JSON.parse(JSON.stringify(codes)),
  };
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
