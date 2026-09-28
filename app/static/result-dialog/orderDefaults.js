/**
 * 工具条上「唯凯站点 / 服务方式 / 运输种类」该显示什么值。
 *
 * 取值优先级（高 → 低），集中在这里，避免各组件各写一遍导致口径漂移：
 *
 * 1. **任务草稿**：这个任务的这几项已被人工核对或改过（draftForms 按任务存）；
 * 2. **上传 context**：调用方（官网/客服）上传托书时带来的订单上下文，代表**这份文件
 *    本身**的业务（不同文件可能来自不同站点、不同业务），比"某人的默认设置"更贴近事实；
 * 3. **用户默认设置**：poOrder 用户设置模板（`type=110` 的 `mawbAddArea` /
 *    `mawbAddSystem`），即"这个操作员通常怎么做单"；
 * 4. **内置兜底**：都没给时用演示默认值，交给人工核对。
 *
 * 本模块只做纯计算（草稿那一层的覆盖在 useResultDialog.loadTask 里做）。
 */

// 内置兜底值（同 state.js 里 order 的初值）：上海 / 空运 / 出口 / 唯凯配舱。
// 订舱操作（czlx）没有对应的用户默认设置，业务默认值就是「自货（唯凯配舱）」，
// 放在这里一起当基线，工具条四项的口径就完全一致了
export const FALLBACK_ORDER_DEFAULTS = {
  area: "上海",
  opersystemdom: "空运",
  opersystem: "出口",
  czlx: "自货",
};

// 参与解析的字段，名字与提交报文一致（`opersystem` = 运输种类，`opersystemdom` = 服务方式）
export const ORDER_DEFAULT_KEYS = ["area", "opersystemdom", "opersystem", "czlx"];

/** 取第一个非空（去空白后）值；都为空则返回空串。 */
function pick(...values) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) {
      return text;
    }
  }
  return "";
}

/**
 * 按优先级解析工具条三项。
 *
 * @param {{ context?: object, userDefaults?: object|null }} options
 *   `context` 是任务的上传上下文（`status.context`，形如 `{area, opersystem, opersystemdom}`）；
 *   `userDefaults` 是 `/analysis/user-defaults` 的返回（形如 `{area, opersystem, opersystemdom}`）。
 * @returns {{ area: string, opersystemdom: string, opersystem: string }}
 */
export function resolveOrderDefaults({ context = {}, userDefaults = null } = {}) {
  const resolved = {};
  for (const key of ORDER_DEFAULT_KEYS) {
    resolved[key] = pick(
      context?.[key],
      userDefaults?.[key],
      FALLBACK_ORDER_DEFAULTS[key],
    );
  }
  return resolved;
}

/** 工具条三项是否与基线不同（= 人改过）。 */
export function orderDiffersFromBase(order, base) {
  if (!base) {
    return false;
  }
  return ORDER_DEFAULT_KEYS.some(
    (key) => String(order?.[key] ?? "").trim() !== String(base[key] ?? "").trim(),
  );
}

/**
 * 把某任务"人工改过的字段"叠到**当前**默认值基线上。
 *
 * 只叠加确实被人改过的字段：`savedBase` 是保存这份草稿当时的基线，字段与它相同就说明
 * 没人动过，应当跟随最新的默认值（否则改了 poOrder 默认设置后，凡是打开过的任务都会被
 * 永久钉在旧值上 —— 表现为"有时生效有时无效"）。
 *
 * `savedBase` 为空（早期版本只存整份快照、没存基线）时，用**内置兜底值**当参照：
 * 等于兜底值的字段视为"没改过"（跟随最新基线），其余照旧沿用。否则首批上线前打开过的
 * 任务会被永久钉在那时的默认值上，用户改完 poOrder 默认设置后仍然"有时生效有时无效"。
 */
export function mergeOrderDelta({ base, savedOrder = null, savedBase = null } = {}) {
  const merged = { ...base };
  if (!savedOrder) {
    return merged;
  }
  const reference = savedBase || FALLBACK_ORDER_DEFAULTS;
  for (const key of ORDER_DEFAULT_KEYS) {
    const savedText = String(savedOrder?.[key] ?? "").trim();
    if (!savedText) {
      // 保存时该字段是空的：没有"人工值"可沿用，保持基线
      continue;
    }
    if (savedText !== String(reference?.[key] ?? "").trim()) {
      merged[key] = savedText;
    }
  }
  return merged;
}
