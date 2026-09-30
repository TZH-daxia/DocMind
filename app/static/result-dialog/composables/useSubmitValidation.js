import { FIELD_CONTROLS, REQUIRED_FIELDS, TOTAL_FIELD_KEY } from "../constants.js";
import {
  buildTotalValue,
  isValidNumericField,
  numericFieldErrorText,
} from "../fields.js";

function isValidIsoDate(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) {
    return false;
  }
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

// 需要「主数据下拉选中」的控件：委托客户（提交客户 ID）与始发/目的港（提交三字码）。
// 主数据可用时值必须来自候选；主数据不可用时组合框已退化为手工填写（degraded），
// 此时只能手输，放行——否则未配置主数据的环境整个功能不可用
const SELECTION_CONTROLS = ["customer", "port"];
const SELECTION_ERRORS = {
  customer: "请从下拉候选中选择委托客户（提交的是客户 ID）",
  port: "请从下拉候选中选择港口（提交的是三字码）",
};

export function validateBeforeSubmit(form, fieldSelections = {}) {
  const errors = {};
  for (const key of REQUIRED_FIELDS) {
    // 预计运费总额没有独立输入框，由重量与单价派生，单独校验
    if (key === TOTAL_FIELD_KEY) {
      continue;
    }
    const value = String(form[key] ?? "").trim();
    if (!value) {
      errors[key] = true;
      continue;
    }
    const control = FIELD_CONTROLS[key];
    // 日期必须是可识别的 YYYY-MM-DD（含真实日历校验，2 月 30 日会被拦下）
    if (control === "date" && !isValidIsoDate(value)) {
      errors[key] = true;
      continue;
    }
    // 数值字段（件数 / 毛重 / 体积 / 运费单价）：必须是正的普通数字，
    // 整数控件不接受小数；0、负数、科学计数、乱码一律拦在这里。理由随字段行内展示
    if (
      (control === "number" || control === "integer") &&
      !isValidNumericField(key, value)
    ) {
      errors[key] = numericFieldErrorText(key);
      continue;
    }
    // 组合框：主数据可用时，值必须是下拉选中的（confirmed）——手输未选、或抽取值
    // 对不上主数据（unconfirmed）都不许提交，否则会把客户名/港口全名当成客户 ID
    // 或三字码发出去。只在拿到明确的 unconfirmed 时拦：状态缺失（组件尚未上报）
    // 按放行处理，避免把「还没同步」误当成「没选」
    if (
      SELECTION_CONTROLS.includes(control) &&
      fieldSelections[key] === "unconfirmed"
    ) {
      errors[key] = SELECTION_ERRORS[control];
    }
  }
  if (REQUIRED_FIELDS.includes(TOTAL_FIELD_KEY) && !buildTotalValue(form)) {
    errors[TOTAL_FIELD_KEY] = true;
  }
  return errors;
}

export function firstErrorField(errors) {
  return REQUIRED_FIELDS.find((key) => errors[key]) || null;
}
