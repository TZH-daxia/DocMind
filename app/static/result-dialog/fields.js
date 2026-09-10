import {
  FIELD_CONTROLS,
  FIELD_LABELS,
  FIELD_ORDER,
  PARTY_FIELDS,
  PARTY_LABELS,
  REQUIRED_FIELDS,
  TOTAL_FIELD_KEY,
} from "./constants.js";

function toNumber(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function buildTotalValue(form) {
  const weight = toNumber(form.ybweight);
  const price = toNumber(form.inwageallinprice);
  if (weight === null || price === null) {
    return "";
  }
  return String(Number((weight * price).toFixed(2)));
}

function buildFieldRows(form, fieldStatus) {
  const rows = [];
  for (const key of FIELD_ORDER) {
    const control = FIELD_CONTROLS[key] || "text";
    const status = fieldStatus[key] || "";
    if (control === "party") {
      // 发货人/收货人拆成名称、地址、电话、邮箱四行，与其它字段同一行结构，
      // 输入框左边界自然对齐；状态标记只在首个字段行上展示一次
      PARTY_FIELDS.forEach((item, index) => {
        rows.push({
          id: `${key}-${item.key}`,
          fieldKey: key,
          subKey: item.key,
          label: `${PARTY_LABELS[key] || FIELD_LABELS[key]}${item.suffix}`,
          control: "text",
          multiline: item.multiline,
          status: index === 0 ? status : "",
        });
      });
      continue;
    }
    rows.push({
      id: key,
      fieldKey: key,
      subKey: null,
      label: FIELD_LABELS[key] || key,
      control,
      multiline: false,
      status,
      required: REQUIRED_FIELDS.includes(key),
    });
    if (key === "inwageallinprice") {
      rows.push({
        id: TOTAL_FIELD_KEY,
        fieldKey: TOTAL_FIELD_KEY,
        subKey: null,
        label: FIELD_LABELS[TOTAL_FIELD_KEY],
        control: "computed",
        multiline: false,
        status: "",
        required: REQUIRED_FIELDS.includes(TOTAL_FIELD_KEY),
        value: buildTotalValue(form),
      });
    }
  }
  return rows;
}

export { buildFieldRows, buildTotalValue };
