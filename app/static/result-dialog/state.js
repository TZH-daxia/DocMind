const { reactive } = window.Vue;

export const dialogState = reactive({
  visible: false,
  taskId: null,
  fileName: "",
  taskStatus: "",
  loading: false,
  error: "",
  pageUrls: [],
  form: {},
  original: {},
  // 控件渲染不出来的原值（如日期区间），仅作提示，不参与校验
  rawValues: {},
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
  dialogState.locations = {};
  dialogState.portCandidates = {};
  dialogState.focusedLocationKey = "";
  dialogState.highlightBoxes = [];
  dialogState.highlightStatus = "";
  dialogState.fieldStatus = {};
  dialogState.errors = {};
}
