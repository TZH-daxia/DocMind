const state = {
  files: [],
  selectedTaskId: null,
  eventSource: null,
  events: new Map(),
};
const nodes = [
  ["render_document", "文档转图片"],
  ["read_images_with_vlm", "图片视觉识别"],
  ["extract_candidates", "字段候选提取"],
  ["build_result", "置信度判断与输出"],
];
const MAX_UPLOAD_FILES = 5;
const elements = {
  fileInput: document.querySelector("#fileInput"),
  fileList: document.querySelector("#fileList"),
  fileCount: document.querySelector("#fileCount"),
  refreshButton: document.querySelector("#refreshButton"),
  uploadDropzone: document.querySelector("#uploadDropzone"),
  filePanel: document.querySelector(".file-panel"),
  selectedName: document.querySelector("#selectedName"),
  selectedTaskId: document.querySelector("#selectedTaskId"),
  selectedStatus: document.querySelector("#selectedStatus"),
  cancelButton: document.querySelector("#cancelButton"),
  progressBar: document.querySelector("#progressBar"),
  progressLabel: document.querySelector("#progressLabel"),
  progressPercent: document.querySelector("#progressPercent"),
  eventList: document.querySelector("#eventList"),
  resultSummary: document.querySelector("#resultSummary"),
  resultJson: document.querySelector("#resultJson"),
  resultError: document.querySelector("#resultError"),
  copyButton: document.querySelector("#copyButton"),
  prepareButton: document.querySelector("#prepareButton"),
  toast: document.querySelector("#toast"),
};

// 失败原因分类：标题与列表短标按后端下发的错误码映射，正文直接用 error.message
const ERROR_TITLES = {
  DOCUMENT_TYPE_MISMATCH: "不是空运托书文件",
  EMPTY_EXTRACTION: "未识别到托书内容",
  VLM_VISION_UNAVAILABLE: "图片识别失败",
  TASK_INTERRUPTED: "任务被中断",
  ANALYSIS_FAILED: "分析失败",
};
const ERROR_SHORT_LABELS = {
  DOCUMENT_TYPE_MISMATCH: "非托书文件",
  EMPTY_EXTRACTION: "未识别内容",
  VLM_VISION_UNAVAILABLE: "识别失败",
  TASK_INTERRUPTED: "已中断",
  ANALYSIS_FAILED: "分析失败",
};
// 已知错误码的固定正文（优先于后端 message）：历史任务的 error 里存的可能还是
// 技术文案，这里覆盖掉，保证界面上永远是面向用户的说法
const ERROR_BODIES = {
  DOCUMENT_TYPE_MISMATCH:
    "该文件不像空运托书：未识别到托运人、起讫港、件数等关键内容，请重新上传空运托书/托单（Booking）文件",
  EMPTY_EXTRACTION:
    "没有从文件中识别出任何托书字段，请确认上传的是空运托书/托单（Booking）文件后重新上传",
};
const nodeRows = new Map();
const displayedStates = new Map();
let flushTimer = null;
let latestProgressEvent = null;
document.addEventListener("DOMContentLoaded", () => {
  elements.fileInput.addEventListener("change", onFileSelected);
  elements.refreshButton.addEventListener("click", loadFiles);
  elements.cancelButton.addEventListener("click", cancelSelectedTask);
  elements.copyButton.addEventListener("click", copyResult);
  elements.prepareButton.addEventListener("click", openPrepareDialog);
  document.addEventListener("docmind:toast", onDialogToast);
  initEventList();
  initDragAndDrop();
  loadFiles();
});
function onDialogToast(event) {
  const detail = event.detail || {};
  showToast(detail.message || "", detail.type || "");
}
async function loadFiles() {
  try {
    const payload = await analysisApi.listFiles();
    state.files = payload.items || [];
    renderFileList();
    if (state.selectedTaskId) {
      const selected = state.files.find((item) => item.task_id === state.selectedTaskId);
      if (selected) {
        await selectTask(selected.task_id);
      }
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}
async function onFileSelected(event) {
  const files = Array.from(event.target.files || []);
  event.target.value = "";
  await uploadFiles(files);
}
async function uploadFiles(files) {
  if (!files.length) return;
  if (files.length > MAX_UPLOAD_FILES) {
    showToast(`一次最多选择 ${MAX_UPLOAD_FILES} 个文件，本次已取消，请重新选择`, "error");
    return;
  }
  let lastTaskId = null;
  for (const file of files) {
    try {
      const task = await analysisApi.uploadFile(file, {});
      lastTaskId = task.task_id;
      showToast(`已上传：${file.name}`, "success");
    } catch (error) {
      showToast(`${file.name} 上传失败：${error.message}`, "error");
    }
  }
  if (lastTaskId) {
    await loadFiles();
    await selectTask(lastTaskId);
  }
}
function initDragAndDrop() {
  const dropzone = elements.uploadDropzone;
  ["dragenter", "dragover"].forEach((type) =>
    elements.filePanel.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    }),
  );
  ["dragleave", "drop"].forEach((type) =>
    elements.filePanel.addEventListener(type, () => dropzone.classList.remove("dragover")),
  );
  elements.filePanel.addEventListener("drop", (event) => {
    event.preventDefault();
    uploadFiles(Array.from(event.dataTransfer?.files || []));
  });
  // 防止把文件拖到面板外时浏览器直接打开文件
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());
}
function cancelEventFlush() {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  latestProgressEvent = null;
}
async function selectTask(taskId) {
  state.selectedTaskId = taskId;
  renderFileList();
  stopEvents();
  cancelEventFlush();
  const item = state.files.find((file) => file.task_id === taskId);
  if (!item) return;
  elements.selectedName.textContent = item.original_name;
  elements.selectedTaskId.textContent = item.task_id;
  initEventList();
  renderEvents(state.events.get(taskId) || [], false, item.status);
  renderStatus(item.status, item.progress, item.current_stage, item.error_code);
  renderResult(null, item.status, failedError(item));
  if (item.status === "failed") {
    await loadTaskError(taskId);
  }
  if (item.result_available) {
    try {
      const result = await analysisApi.getResult(taskId);
      renderResult(result, result.overall_status);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  if (item.status === "running" || item.status === "queued") {
    subscribeToTask(taskId);
  } else {
    await loadTaskHistory(taskId);
  }
}
function subscribeToTask(taskId) {
  stopEvents();
  state.eventSource = analysisApi.subscribe(
    taskId,
    handleTaskMessage,
    () => {
      if (state.selectedTaskId === taskId) {
        showToast("进度连接已断开，可刷新查看状态", "error");
      }
    },
  );
}
function handleTaskMessage(message) {
  if (message.kind === "workflow") {
    const event = message.event;
    const taskEvents = state.events.get(event.task_id) || [];
    taskEvents.push(event);
    state.events.set(event.task_id, taskEvents);
    latestProgressEvent = event;
    scheduleEventFlush();
    return;
  }
  if (message.kind === "status") {
    // 先同步本地列表状态：flushEvents 里的事件渲染要据此判断任务是否已取消
    const currentTask = state.files.find((file) => file.task_id === message.task_id);
    if (currentTask) currentTask.status = message.status;
    flushEvents();
    renderStatus(message.status, message.progress, message.current_stage, message.error?.code);
    if (["ready", "needs_review", "failed", "cancelled"].includes(message.status)) {
      stopEvents();
      const finalStatus = message.status;
      if (finalStatus === "failed") {
        renderResult(null, "failed", message.error || null);
      }
      setTimeout(() => {
        loadFiles();
        // 失败/取消都没有结果可载入
        if (finalStatus !== "failed" && finalStatus !== "cancelled") {
          loadSelectedResult();
        }
      }, 2200);
    }
  }
}
function scheduleEventFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    flushEvents();
  }, 140);
}
function flushEvents() {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  const latest = latestProgressEvent;
  latestProgressEvent = null;
  renderEvents(state.events.get(state.selectedTaskId) || [], true, selectedTaskStatus());
  if (latest) {
    renderStatus(null, latest.progress, latest.message);
  }
}
async function loadSelectedResult() {
  if (!state.selectedTaskId) return;
  try {
    const result = await analysisApi.getResult(state.selectedTaskId);
    renderResult(result, result.overall_status);
  } catch {
    renderResult(null, "unknown");
  }
}
async function loadTaskHistory(taskId) {
  try {
    const payload = await analysisApi.getEvents(taskId);
    const items = payload.items || [];
    state.events.set(taskId, items);
    renderEvents(items, true, selectedTaskStatus());
  } catch (error) {
    showToast(`执行历史加载失败：${error.message}，可稍后刷新重试`, "error");
  }
}
// 当前选中任务的最新状态：事件渲染据此判断任务是否已取消
function selectedTaskStatus() {
  const item = state.files.find((file) => file.task_id === state.selectedTaskId);
  return item?.status || null;
}
function renderFileList() {
  elements.fileCount.textContent = state.files.length;
  if (!state.files.length) {
    elements.fileList.innerHTML = '<div class="empty-state">还没有上传文件</div>';
    return;
  }
  elements.fileList.innerHTML = state.files
    .map((item) => {
      const selected = item.task_id === state.selectedTaskId ? " selected" : "";
      return `
        <button class="file-item${selected}" data-task-id="${escapeHtml(item.task_id)}">
          <span class="file-type">${escapeHtml(getExtension(item.original_name))}</span>
          <span class="file-copy">
            <strong>${escapeHtml(item.original_name)}</strong>
            <small>${escapeHtml(item.current_stage || "等待运行")}</small>
            ${
              item.status === "cancelled"
                ? '<em class="file-flag">已取消</em>'
                : item.status === "failed" && ERROR_SHORT_LABELS[item.error_code]
                  ? `<em class="file-flag">${escapeHtml(ERROR_SHORT_LABELS[item.error_code])}</em>`
                  : ""
            }
          </span>
          <span class="status-dot ${statusClass(item.status)}"></span>
        </button>`;
    })
    .join("");
  elements.fileList.querySelectorAll(".file-item").forEach((button) => {
    button.addEventListener("click", () => selectTask(button.dataset.taskId));
  });
}
function renderStatus(status, progress, label, errorCode) {
  if (status) {
    const text = statusText(status, errorCode);
    elements.selectedStatus.textContent = text;
    elements.selectedStatus.className = `status-pill ${statusClass(status)}`;
    elements.selectedStatus.title = text;
    updateCancelButton(status);
  }
  const safeProgress = Number.isFinite(Number(progress)) ? Number(progress) : 0;
  elements.progressBar.style.width = `${Math.max(0, Math.min(100, safeProgress))}%`;
  elements.progressPercent.textContent = `${safeProgress}%`;
  elements.progressLabel.textContent = label || "等待运行";
}
// 「停止任务」只在任务仍可被中断时可用：终态（含已取消）不给点
function updateCancelButton(status) {
  elements.cancelButton.disabled = !["queued", "running"].includes(status);
}
function initEventList() {
  elements.eventList.innerHTML = "";
  nodeRows.clear();
  displayedStates.clear();
  nodes.forEach(([key, label]) => buildNodeRow(key, label));
}
function buildNodeRow(key, label) {
  const row = document.createElement("div");
  row.className = "event-row";
  row.innerHTML = `
    <span class="event-marker idle"></span>
    <div class="event-copy">
      <strong>${escapeHtml(label)}</strong>
      <small></small>
    </div>
    <span class="event-value"></span>`;
  elements.eventList.appendChild(row);
  nodeRows.set(key, {
    marker: row.querySelector(".event-marker"),
    message: row.querySelector(".event-copy small"),
    value: row.querySelector(".event-value"),
  });
  displayedStates.set(key, "idle");
}
function renderEvents(taskEvents, animate = false, taskStatus = null) {
  const grouped = new Map(
    nodes.map(([key, label]) => [key, { key, label, state: "idle", message: "", duration: null }]),
  );
  taskEvents.filter((event) => event && event.node_name).forEach((event) => {
    if (!grouped.has(event.node_name)) {
      grouped.set(event.node_name, {
        key: event.node_name,
        label: event.node_name,
        state: "idle",
        message: "",
        duration: null,
      });
    }
    const target = grouped.get(event.node_name);
    target.state = event.event_type;
    target.progress = event.progress;
    target.message = event.message;
    target.duration = event.duration_ms;
  });
  // 任务被取消后不可能还有节点在跑：只留下 "started"（没有对应的完成事件）
  // 的节点是被打断的，退回未执行态显示"等待执行"，避免界面停留在"开始执行"
  // 让人误以为任务仍在继续
  if (taskStatus === "cancelled") {
    grouped.forEach((item) => {
      if (item.state === "started") {
        item.state = "idle";
        item.message = "";
        item.duration = null;
      }
    });
  }
  let delayIndex = 0;
  let failureSeen = false;
  grouped.forEach((item) => {
    if (item.state === "failed") {
      failureSeen = true;
    } else if (item.state === "idle" && failureSeen) {
      item.state = "skipped";
      item.message = "前置节点失败，已跳过";
    }
    if (!nodeRows.has(item.key)) {
      buildNodeRow(item.key, item.label);
    }
    const row = nodeRows.get(item.key);
    row.message.textContent = item.message || (item.state === "idle" ? "等待执行" : "—");
    row.value.textContent = item.duration
      ? `${item.duration} ms`
      : item.state === "succeeded"
        ? "完成"
        : item.state === "skipped"
          ? "已跳过"
          : "";
    if (displayedStates.get(item.key) === item.state) {
      row.marker.className = `event-marker ${item.state}`;
      return;
    }
    const delay = animate ? delayIndex * 200 : 0;
    delayIndex += 1;
    row.marker.style.transitionDelay = `${delay}ms`;
    row.marker.style.animationDelay = `${delay}ms`;
    row.marker.classList.remove("just-lit");
    void row.marker.offsetWidth;
    row.marker.className = `event-marker ${item.state}`;
    if (animate && item.state !== "idle") {
      row.marker.classList.add("just-lit");
    }
    displayedStates.set(item.key, item.state);
  });
}
function renderResult(result, status, error) {
  // 「准备提交」仅在任务完成且有结果时点亮，运行中/失败/未选任务一律置灰
  elements.prepareButton.disabled = !isResultReady(result, status);
  renderResultError(status, error);
  if (!result) {
    // 不再显示"暂无结果"占位：失败原因由错误卡片说明，运行中由进度区说明
    elements.resultSummary.hidden = true;
    elements.resultSummary.innerHTML = "";
    elements.resultJson.innerHTML = "<code>{}</code>";
    elements.copyButton.disabled = true;
    return;
  }
  elements.resultSummary.hidden = false;
  const reviewStatuses = ["needs_review", "conflict", "missing", "invalid"];
  const reviewFields = Array.isArray(result.review_fields)
    ? result.review_fields
    : Object.entries(result.field_meta || {})
        .filter(([, meta]) => meta && reviewStatuses.includes(meta.status))
        .map(([key]) => key);
  elements.resultSummary.innerHTML = `
    <span class="result-status ${statusClass(status)}">${escapeHtml(statusText(status))}</span>
    <span>置信度 ${Math.round((result.overall_confidence || 0) * 100)}%</span>`;
  const reviewNote = reviewFields.length
    ? `<div class="review-note">以下字段待人工审核：${escapeHtml(reviewFields.join("、"))}</div>`
    : "";
  elements.resultJson.innerHTML = `<code>${escapeHtml(JSON.stringify(result.result || {}, null, 2))}</code>${reviewNote}`;
  elements.copyButton.disabled = false;
}
function failedError(item) {
  if (!item || item.status !== "failed" || !item.error_code) return null;
  return { code: item.error_code, message: item.error_message || "" };
}
function renderResultError(status, error) {
  const code = status === "failed" ? error?.code : null;
  if (!code) {
    elements.resultError.hidden = true;
    elements.resultError.innerHTML = "";
    return;
  }
  const title = ERROR_TITLES[code] || "分析失败";
  const body = ERROR_BODIES[code] || error.message || title;
  const hint = error.hint
    ? `<p class="result-error-hint">识别到的内容：${escapeHtml(error.hint)}</p>`
    : "";
  elements.resultError.innerHTML = `
    <strong class="result-error-title">${escapeHtml(title)}</strong>
    <p class="result-error-body">${escapeHtml(body)}</p>
    ${hint}`;
  elements.resultError.hidden = false;
}
async function loadTaskError(taskId) {
  try {
    const detail = await analysisApi.getTask(taskId);
    if (state.selectedTaskId !== taskId || !detail.error?.code) return;
    renderStatus(detail.status, detail.progress, detail.current_stage, detail.error.code);
    renderResult(null, detail.status, detail.error);
  } catch (error) {
    // 详情拿不到时保留列表项里的兜底信息，不打扰用户
  }
}
async function cancelSelectedTask() {
  const taskId = state.selectedTaskId;
  if (!taskId) return;
  elements.cancelButton.disabled = true;
  try {
    await analysisApi.cancelTask(taskId);
    showToast("已停止任务，取消后不可恢复", "success");
    await loadFiles();
    await selectTask(taskId);
  } catch (error) {
    showToast(`停止失败：${error.message}`, "error");
    // 失败时恢复按钮，避免用户误以为任务已停
    elements.cancelButton.disabled = false;
  }
}
async function copyResult() {
  const content = elements.resultJson.textContent;
  if (!content || content === "{}") return;
  await navigator.clipboard.writeText(content);
  showToast("JSON 已复制", "success");
}
function isResultReady(result, status) {
  return Boolean(result && result.task_id) && status !== "failed";
}
async function openPrepareDialog() {
  const taskId = state.selectedTaskId;
  if (!taskId) return;
  const item = state.files.find((file) => file.task_id === taskId);
  if (!item?.result_available) {
    showToast("结果尚未生成，暂时无法准备提交", "error");
    return;
  }
  await window.DocMindResultDialog?.open(taskId);
}
function stopEvents() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}
function statusText(status, errorCode) {
  if (status === "failed" && errorCode && ERROR_TITLES[errorCode]) {
    return ERROR_TITLES[errorCode];
  }
  return {
    queued: "待运行",
    running: "运行中",
    ready: "已完成",
    needs_review: "待人工审核",
    failed: "失败",
    cancelled: "已取消",
    unknown: "未知",
  }[status] || "未运行";
}
function statusClass(status) {
  return {
    queued: "queued",
    running: "running",
    ready: "ready",
    needs_review: "review",
    failed: "failed",
    cancelled: "cancelled",
  }[status] || "idle";
}
function getExtension(filename) {
  return filename.split(".").pop()?.toUpperCase() || "FILE";
}
function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
let toastTimer;
function showToast(message, type) {
  elements.toast.textContent = message;
  elements.toast.className = `toast visible ${type || ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    elements.toast.className = "toast";
  }, 2800);
}
