import { FIELD_CONTROLS, REQUIRED_FIELDS, TOTAL_FIELD_KEY } from "../constants.js";
import { buildTotalValue } from "../fields.js";

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

export function validateBeforeSubmit(form) {
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
    // 日期必须是可识别的 YYYY-MM-DD（含真实日历校验，2 月 30 日会被拦下）
    if (FIELD_CONTROLS[key] === "date" && !isValidIsoDate(value)) {
      errors[key] = true;
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
