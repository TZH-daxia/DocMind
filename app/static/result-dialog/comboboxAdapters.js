import { searchCustomers, searchPorts } from "./api.js";

// 委托客户：候选来自客户主数据，选中后回填客户 ID（与 poOrder 一致）。
// 注：客户主数据（PubFCustom）没有站点字段、接口也不按站点收敛（实测 area 参数
// 传空/上海/宁波返回条数都是 14706 条），所以候选不做站点过滤。
// poOrder 里按站点过滤的是「委托项目」wtxm / 供应商等，不是委托客户
export const CUSTOMER_COMBOBOX = {
  noun: "客户",
  search: searchCustomers,
  toValue: (item) => String(item.id),
  toPrimary: (item) => item.usr_name || String(item.id),
  toMeta: (item) => item.usr_code || "",
  toDisabled: (item) => item.available === false,
  disabledMeta: "已停用",
  disabledTitle: "客户已停用或不参与新业务，不可选择",
  placeholder: "输入客户名称 / 编码搜索",
  degradedPlaceholder: "请输入委托客户",
  selectedTitle: (item) =>
    `已选：${item.usr_name}${item.usr_code ? `（${item.usr_code}）` : ""}，提交值 ID：${item.id}`,
  pendingTitle: "请从下拉候选中选择委托客户（提交的是客户 ID）",
};

// 始发港/目的港：候选来自港口主数据，选中后回填三字码（输入框直接显示三字码）
export const PORT_COMBOBOX = {
  noun: "港口",
  search: searchPorts,
  toValue: (item) => item.three_code,
  toPrimary: (item) => item.three_code,
  toMeta: (item) => item.english_name || "",
  placeholder: "输入三字码 / 英文港口名搜索",
  degradedPlaceholder: "请输入三字码",
  selectedTitle: (item) =>
    `已选：${item.three_code}${item.english_name ? `（${item.english_name}）` : ""}`,
  pendingTitle: "请从下拉候选中选择港口（选中后填入三字码）",
};
