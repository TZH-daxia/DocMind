import { FALLBACK_ORDER_DEFAULTS } from "./orderDefaults.js";
import { DEFAULT_SERVICE_CODES } from "./serviceItems.js";

const { reactive } = window.Vue;

export const dialogState = reactive({
  visible: false,
  taskId: null,
  fileName: "",
  // 弹窗头部页签条：最近已解析完成的文件 [{ taskId, name }]
  fileTabs: [],
  taskStatus: "",
  loading: false,
  error: "",
  pageUrls: [],
  form: {},
  original: {},
  // 控件渲染不出来的原值（如日期区间），仅作提示，不参与校验
  rawValues: {},
  // 后端给出的原文证据：field key -> [引用片段]，待审核字段在输入框下展示
  evidences: {},
  // 后端定位结果：target（字段 key 或 key.subkey）-> [{page, bbox}]
  locations: {},
  // 港口归一化未定论时的候选：field key -> [{three_code, english_name, country_code}]
  portCandidates: {},
  focusedLocationKey: "",
  highlightBoxes: [],
  highlightStatus: "",
  fieldStatus: {},
  errors: {},
  // 站点字典候选（工具条「委托唯凯站点」下拉的分组数据）：进程级参考数据，
  // 与任务无关，因此不在 resetDialogContent 里清空；用 siteGroupsLoaded
  // 区分「还没拉」和「拉过但字典未接入（groups 为空）」，避免反复请求
  siteGroups: [],
  siteGroupsLoaded: false,
  // 订单级上下文（工具条上的单号 / 唯凯站点 / 服务方式 / 运输种类 / 订舱操作）：
  // 四项默认值按「上传 context > 用户默认设置 > 内置兜底」解析，再叠加该任务
  // **人工改过**的字段（见 orderDefaults.js），这里是最后一级兜底；打开任务时由
  // loadTask 覆盖。订舱操作兜底「自货（唯凯配舱）」与 poOrder 订单新增页一致，
  // 且该值要进提交报文的 czlx，不能为空（提交前还有一道兜底校验，见 useResultDialog.submit）
  order: {
    code: "",
    ...FALLBACK_ORDER_DEFAULTS,
  },
  // 服务项目面板里勾选的服务代码（提交报文 serviceList 的来源）。
  // 默认勾上唯凯配舱（OA0010）——与 poOrder 订单新增页的默认一致；
  // 与表单 / 订单上下文一样按任务持久化（见 draftForms.js），切任务 / 刷新后不丢
  serviceCodes: [...DEFAULT_SERVICE_CODES],
  // 提交成功后 poOrder 返回的订舱编号（按任务持久化，见 submittedTasks.js）：
  // 显示在弹窗头部、叉号左侧。不放工具条的编号槽——那里是「单据编号」的位置，
  // 17 位订舱编号会把右侧四个胶囊挤出去造成遮挡
  orderCode: "",
  submitting: false,
  // 当前任务是否已提交成功：提交按钮据此显示「已提交」并禁止再次点击
  submitted: false,
});

export function resetDialogContent() {
  dialogState.taskId = null;
  dialogState.fileName = "";
  dialogState.taskStatus = "";
  dialogState.loading = false;
  dialogState.error = "";
  dialogState.pageUrls = [];
  dialogState.form = {};
  dialogState.original = {};
  dialogState.rawValues = {};
  dialogState.evidences = {};
  dialogState.locations = {};
  dialogState.portCandidates = {};
  dialogState.focusedLocationKey = "";
  dialogState.highlightBoxes = [];
  dialogState.highlightStatus = "";
  dialogState.fieldStatus = {};
  dialogState.errors = {};
  // 与初始值保持一致：四项回到内置兜底（打开下一个任务时由 loadTask 重算）
  dialogState.order = {
    code: "",
    ...FALLBACK_ORDER_DEFAULTS,
  };
  // 服务项目回到默认勾选（唯凯配舱）
  dialogState.serviceCodes = [...DEFAULT_SERVICE_CODES];
  dialogState.orderCode = "";
  dialogState.submitted = false;
}
