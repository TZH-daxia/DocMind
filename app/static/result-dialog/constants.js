export const FIELD_ORDER = [
  "fid",
  "sfg",
  "mdg",
  "ybpiece",
  "ybweight",
  "ybvolume",
  "hbrq",
  "inwageallinprice",
  "chinesepm",
  "englishpm",
  "shipper",
  "consignee",
];

// 仅前端展示的派生字段：不进抽取结果，由重量 × 预计运费单价自动计算
export const TOTAL_FIELD_KEY = "inwagealltotal";

export const FIELD_LABELS = {
  fid: "委托客户",
  sfg: "始发港（三字码/港口名称）",
  mdg: "目的港（三字码/港口名称）",
  ybpiece: "件数",
  ybweight: "实际毛重（公斤）",
  ybvolume: "总体积（CBM）",
  hbrq: "预计航班日期",
  inwageallinprice: "预计运费单价（CNY）",
  [TOTAL_FIELD_KEY]: "预计运费总额",
  chinesepm: "中文品名",
  englishpm: "英文品名",
  shipper: "发货人",
  consignee: "收货人",
};

export const FIELD_CONTROLS = {
  // 委托客户从客户主数据下拉选取回填客户 ID；始发港/目的港从港口主数据下拉
  // 选取回填三字码（见 comboboxAdapters.js 与 SearchCombobox.js）
  fid: "customer",
  sfg: "port",
  mdg: "port",
  ybpiece: "integer",
  ybweight: "number",
  ybvolume: "number",
  inwageallinprice: "number",
  hbrq: "date",
  chinesepm: "textarea",
  englishpm: "textarea",
  shipper: "party",
  consignee: "party",
};

export const PARTY_LABELS = {
  shipper: "发货人",
  consignee: "收货人",
};

export const PARTY_FIELDS = [
  { key: "name", suffix: "名称", multiline: false },
  { key: "address", suffix: "地址", multiline: true },
  { key: "phone", suffix: "电话", multiline: false },
  { key: "email", suffix: "邮箱", multiline: false },
];

// 提交校验的必填项：8 个后端必填字段 + 前端派生的预计运费总额
export const REQUIRED_FIELDS = [
  "fid",
  "sfg",
  "mdg",
  "ybpiece",
  "ybweight",
  "ybvolume",
  "hbrq",
  "inwageallinprice",
  TOTAL_FIELD_KEY,
];

export const FIELD_STATUS_LABELS = {
  // conflict（文档中同一字段出现不一致的值）与 needs_review 对使用者来说是同一件事：
  // 都要人工确认，因此合并为同一个「待审核」标记；具体原因在输入框下方说明
  needs_review: "待审核",
  conflict: "待审核",
  missing: "缺失",
  invalid: "无效",
};

// 需要在输入框下展示"要审核的原文"的状态：与后端 review_statuses 一致
// （missing 没有原文证据，因此天然不会命中）
export const REVIEW_STATUSES = ["needs_review", "conflict", "invalid"];

export const DATE_VALUE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
