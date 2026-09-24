export const FIELD_ORDER = [
  "fid",
  "sfg",
  "mdg",
  "ybpiece",
  "ybweight",
  "ybvolume",
  "hbrq",
  // 进仓编号 / 预报尺寸备注：托书里没有、由操作员手工填写，因此排在航班日期之后，
  // 即在「抽取值 → 人工补录值」的交界处（口径见下方 REQUIRED_FIELDS 的说明）
  "khjcno",
  "ybvolumeremark",
  "inwageallinprice",
  "chinesepm",
  "englishpm",
  "shipper",
  "consignee",
];

// 不在表单表格里、但由横栏上的选择器写进 form、同样属于「人工核对过的内容」的字段：
// 项目（gid 进提交报文；wtxmname / wtxmcode 用于显示与拼单号）与本票客服联系人
//（customerRelList）。它们必须跟草稿一起持久化 —— 否则切任务/刷新后，刚选好的项目
// 就不显示了（草稿里其实存着，只是恢复时只认表格字段 FIELD_ORDER，把这些挡掉了）。
export const CONTEXT_FIELDS = ["gid", "wtxmname", "wtxmcode", "customerRelList"];

// 上述字段的初始值（未列出的按空串），让"没选过"与"选过又被清空"表现一致
export const CONTEXT_FIELD_DEFAULTS = { customerRelList: [] };

// 仅前端展示的派生字段：不进抽取结果，由重量 × 预计运费单价自动计算
export const TOTAL_FIELD_KEY = "inwagealltotal";

export const FIELD_LABELS = {
  fid: "委托客户",
  // 港口标签不带"（三字码/港口名称）"后缀：标签列宽由最宽标签决定，缩短后标签列更窄、
  // 右侧输入框更宽（委托客户下拉菜单宽度跟随输入框，能完整显示推荐公司名）
  sfg: "始发港",
  mdg: "目的港",
  ybpiece: "件数",
  ybweight: "实际毛重（公斤）",
  ybvolume: "总体积（CBM）",
  hbrq: "预计航班日期",
  // 与 poOrder 字段同名同义：进仓编号 = khjcno，预报尺寸备注 = ybvolumeremark
  khjcno: "进仓编号",
  ybvolumeremark: "预报尺寸备注",
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
  // 进仓编号可多值（poOrder 的占位文案是「多个用逗号隔开」），因此用单行文本；
  // 预报尺寸备注在 poOrder 是 textarea（字段配置 type: 17）
  khjcno: "text",
  ybvolumeremark: "textarea",
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

// 提交校验的必填项：8 个后端必填字段 + 前端派生的预计运费总额 + 2 个手工补录字段
//
// 进仓编号（khjcno）在 poOrder 里一律 `required: true`（newOrderAdd.vue 的
// baseInfoInputViewData、service.js 的 homeInformation、houseNumberAdd 明细行），
// 不随业务类型区分，所以这里也按必填处理。
//
// 预报尺寸备注（ybvolumeremark）在 poOrder 是**条件必填**：只有订舱操作
// czlx == '自货'（即「唯凯配舱」）且 opersystemdom != '铁运' 时才必填
// （houseNumberAdd.vue 的 `input-required` 类与 newOrderAdd.vue 的提交校验
//「请填写尺寸备注！」）。这里按当前需求统一设为必填，若以后要跟 poOrder 一致，
// 改成「按 dialogState.order.czlx 判断」即可（字段值本身不用动）。
export const REQUIRED_FIELDS = [
  "fid",
  "sfg",
  "mdg",
  "ybpiece",
  "ybweight",
  "ybvolume",
  "hbrq",
  "khjcno",
  "ybvolumeremark",
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
