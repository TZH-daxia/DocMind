const state = {
  files: [],
  selectedTaskId: null,
  eventSource: null,
  events: new Map(),
};
const nodes = [
  ["render_document", "文档转图片"],
  ["parse_with_mineru", "MinerU 文档解析"],
  ["read_images_with_vlm", "图片视觉识别"],
  ["extract_candidates", "字段候选提取"],
  ["normalize_candidates", "字段标准化"],
  ["validate_candidates", "字段校验"],
  ["resolve_conflicts", "冲突处理"],
  ["calculate_confidence", "置信度计算"],
  ["finalize_result", "生成最终 JSON"],
];
const elements = {
  fileInput: document.querySelector("#fileInput"),
  fileList: document.querySelector("#fileList"),
  fileCount: document.querySelector("#fileCount"),
  refreshButton: document.querySelector("#refreshButton"),
  runButton: document.querySelector("#runButton"),
  runText: document.querySelector("#runText"),
  selectedName: document.querySelector("#selectedName"),
  selectedTaskId: document.querySelector("#selectedTaskId"),
  selectedStatus: document.querySelector("#selectedStatus"),
  progressBar: document.querySelector("#progressBar"),
  progressLabel: document.querySelector("#progressLabel"),
  progressPercent: document.querySelector("#progressPercent"),
  eventList: document.querySelector("#eventList"),
  resultSummary: document.querySelector("#resultSummary"),
  resultJson: document.querySelector("#resultJson"),
  copyButton: document.querySelector("#copyButton"),
  toast: document.querySelector("#toast"),
};
const nodeRows = new Map();
const displayedStates = new Map();
let flushTimer = null;
let latestProgressEvent = null;
document.addEventListener("DOMContentLoaded", () => {
  elements.fileInput.addEventListener("change", onFileSelected);
  elements.refreshButton.addEventListener("click", loadFiles);
  elements.runButton.addEventListener("click", runSelectedTask);
  elements.copyButton.addEventListener("click", copyResult);
  initEventList();
  loadFiles();
});
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
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  try {
    const task = await analysisApi.uploadFile(file, readContext());
    showToast("上传成功，已自动开始分析", "success");
    await loadFiles();
    await selectTask(task.task_id);
  } catch (error) {
    showToast(error.message, "error");
  }
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
  stopEvents();
  cancelEventFlush();
  const item = state.files.find((file) => file.task_id === taskId);
  if (!item) return;
  elements.selectedName.textContent = item.original_name;
  elements.selectedTaskId.textContent = item.task_id;
  elements.runButton.disabled = item.status === "running";
  initEventList();
  renderEvents(state.events.get(taskId) || [], false);
  renderStatus(item.status, item.progress, item.current_stage);
  renderResult(null, item.status);
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
  }
}
async function runSelectedTask() {
  if (!state.selectedTaskId) return;
  try {
    await analysisApi.runTask(state.selectedTaskId);
    state.events.set(state.selectedTaskId, []);
    cancelEventFlush();
    initEventList();
    renderEvents([], false);
    subscribeToTask(state.selectedTaskId);
    setRunRunning(true);
    elements.runButton.disabled = true;
  } catch (error) {
    showToast(error.message, "error");
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
    flushEvents();
    renderStatus(message.status, message.progress, message.current_stage);
    if (["ready", "needs_review", "failed"].includes(message.status)) {
      elements.runButton.disabled = false;
      stopEvents();
      const finalStatus = message.status;
      setTimeout(() => {
        loadFiles();
        if (finalStatus !== "failed") {
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
  renderEvents(state.events.get(state.selectedTaskId) || [], true);
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
          </span>
          <span class="status-dot ${statusClass(item.status)}"></span>
        </button>`;
    })
    .join("");
  elements.fileList.querySelectorAll(".file-item").forEach((button) => {
    button.addEventListener("click", () => selectTask(button.dataset.taskId));
  });
}
function renderStatus(status, progress, label) {
  if (status) {
    elements.selectedStatus.textContent = statusText(status);
    elements.selectedStatus.className = `status-pill ${statusClass(status)}`;
    setRunRunning(status === "running");
  }
  const safeProgress = Number.isFinite(Number(progress)) ? Number(progress) : 0;
  elements.progressBar.style.width = `${Math.max(0, Math.min(100, safeProgress))}%`;
  elements.progressPercent.textContent = `${safeProgress}%`;
  elements.progressLabel.textContent = label || "等待运行";
}
function setRunRunning(running) {
  elements.runButton.classList.toggle("is-running", running);
  elements.runText.textContent = running ? "运行中" : "运行";
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
function renderEvents(taskEvents, animate = false) {
  const grouped = new Map(
    nodes.map(([key, label]) => [key, { key, label, state: "idle", message: "", duration: null }]),
  );
  taskEvents.forEach((event) => {
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
  let delayIndex = 0;
  grouped.forEach((item) => {
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
function renderResult(result, status) {
  if (!result) {
    elements.resultSummary.innerHTML = "<span>暂无结果</span>";
    elements.resultJson.innerHTML = "<code>{}</code>";
    elements.copyButton.disabled = true;
    return;
  }
  const validation = result.validation || {};
  elements.resultSummary.innerHTML = `
    <span class="result-status ${statusClass(status)}">${escapeHtml(statusText(status))}</span>
    <span>置信度 ${Math.round((result.overall_confidence || 0) * 100)}%</span>
    <span>错误 ${validation.errors?.length || 0}</span>
    <span>警告 ${validation.warnings?.length || 0}</span>`;
  elements.resultJson.innerHTML = `<code>${escapeHtml(JSON.stringify(result.result || {}, null, 2))}</code>`;
  elements.copyButton.disabled = false;
}
async function copyResult() {
  const content = elements.resultJson.textContent;
  if (!content || content === "{}") return;
  await navigator.clipboard.writeText(content);
  showToast("JSON 已复制", "success");
}
function readContext() {
  const fidValue = document.querySelector("#client").value;
  return {
    fid: fidValue ? Number(fidValue) : null,
  };
}
function stopEvents() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}
function statusText(status) {
  return {
    queued: "待运行",
    running: "运行中",
    ready: "已完成",
    needs_review: "待检查",
    failed: "失败",
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
