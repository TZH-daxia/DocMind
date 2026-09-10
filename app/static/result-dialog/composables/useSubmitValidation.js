import { REQUIRED_FIELDS, TOTAL_FIELD_KEY } from "../constants.js";
import { buildTotalValue } from "../fields.js";

export function validateBeforeSubmit(form) {
  const errors = {};
  for (const key of REQUIRED_FIELDS) {
    // 预计运费总额没有独立输入框，由重量与单价派生，单独校验
    if (key === TOTAL_FIELD_KEY) {
      continue;
    }
    if (!String(form[key] ?? "").trim()) {
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
