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
  // 来自上传时页面传入的 context，提交前可在这里核对与修正。
  // 唯凯站点 / 服务方式 / 运输种类先给演示默认值（上海 / 空运 / 出口）：
  // context 还没接上，否则打开预览页时这三项是空占位；等 context 接通后由真实值覆盖。
  // 订舱操作默认「自货（唯凯配舱）」：与 poOrder 订单新增页的默认值一致，且该值要进
  // 提交报文的 czlx，不能为空（提交前还有一道兜底校验，见 useResultDialog.submit）
  order: {
    code: "",
    area: "上海",
    opersystemdom: "空运",
    opersystem: "出口",
    czlx: "自货",
  },
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
  // 与初始值保持一致：站点/服务方式/运输种类是演示默认值（上海 / 空运 / 出口），
  // 等 context 接通后被真实值覆盖；订舱操作默认「自货」是业务默认值（同 poOrder）
  dialogState.order = {
    code: "",
    area: "上海",
    opersystemdom: "空运",
    opersystem: "出口",
    czlx: "自货",
  };
  dialogState.orderCode = "";
  dialogState.submitted = false;
}
