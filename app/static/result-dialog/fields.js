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

// ── 数值字段规则 ────────────────────────────────────────────────────────────
// 与后端 app/service/order_submit_service.py 的 validate_numeric_fields 同口径：
// 只认正的普通十进制写法，明确拒绝 0、负号、正号、科学计数（如 1e3）、千分位等；
// 整数控件（件数）不接受小数；小数位上限用来挡住乱码般的长小数。
const NUMBER_PATTERN = /^(?:\d+(?:\.\d*)?|\.\d+)$/;
const INTEGER_PATTERN = /^\d+$/;
const MAX_DECIMAL_PLACES = 6;

/** 数值字段是否合法；非数值控件一律返回 true（不归这里管）。
 *
 * 件数 / 毛重 / 体积 / 运费单价都必须是**正数**：0 与负数一样无意义
 * （0 件、0 公斤、0 体积、0 运费的订单不成立），一律拦下。
 */
function isValidNumericField(key, text) {
  const control = FIELD_CONTROLS[key];
  if (control !== "number" && control !== "integer") {
    return true;
  }
  const value = String(text ?? "").trim();
  if (!value) {
    return false;
  }
  if (control === "integer") {
    return INTEGER_PATTERN.test(value) && Number(value) > 0;
  }
  // "12." 是输入中间态、不是完整数字：按非法处理，提示补全
  if (!NUMBER_PATTERN.test(value) || value.endsWith(".")) {
    return false;
  }
  const decimals = value.includes(".") ? value.split(".", 2)[1] : "";
  return decimals.length <= MAX_DECIMAL_PLACES && Number(value) > 0;
}

/** 数值字段不合法时给操作员看的原因（行内展示）。 */
function numericFieldErrorText(key) {
  if (FIELD_CONTROLS[key] === "integer") {
    return "请填写大于 0 的整数";
  }
  return "请填写大于 0 的数字（不接受负号、科学计数等写法）";
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
          // 与后端 locations[].target 对齐：标量为字段 key，参与人为 key.subkey
          locationKey: `${key}.${item.key}`,
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
      locationKey: key,
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
        locationKey: "",
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

export {
  buildFieldRows,
  buildTotalValue,
  isValidNumericField,
  numericFieldErrorText,
};
