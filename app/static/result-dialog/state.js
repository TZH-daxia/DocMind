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
  dialogState.submitted = false;
}
