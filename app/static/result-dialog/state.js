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
  dialogState.fieldStatus = {};
  dialogState.errors = {};
}
