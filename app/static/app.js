const state = {
  files: [],
  selectedTaskId: null,
  eventSource: null,
  events: new Map(),
};
const nodes = [
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
document.addEventListener("DOMContentLoaded", () => {
  elements.fileInput.addEventListener("change", onFileSelected);
  elements.refreshButton.addEventListener("click", loadFiles);
  elements.runButton.addEventListener("click", runSelectedTask);
  elements.copyButton.addEventListener("click", copyResult);
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
    showToast("文件已加入待运行列表", "success");
    await loadFiles();
    await selectTask(task.task_id);
  } catch (error) {
    showToast(error.message, "error");
  }
}
async function selectTask(taskId) {
  state.selectedTaskId = taskId;
  stopEvents();
  const item = state.files.find((file) => file.task_id === taskId);
  if (!item) return;
  elements.selectedName.textContent = item.original_name;
  elements.selectedTaskId.textContent = item.task_id;
  elements.runButton.disabled = item.status === "running";
  renderStatus(item.status, item.progress, item.current_stage);
  renderEvents(state.events.get(taskId) || []);
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
    renderEvents([]);
    subscribeToTask(state.selectedTaskId);
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
    renderEvents(taskEvents);
    renderStatus(null, event.progress, event.message);
    return;
  }
  if (message.kind === "status") {
    renderStatus(message.status, message.progress, message.current_stage);
    if (["ready", "needs_review", "failed"].includes(message.status)) {
      elements.runButton.disabled = false;
      stopEvents();
      loadFiles();
      if (message.status !== "failed") {
        loadSelectedResult();
      }
    }
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
  }
  const safeProgress = Number.isFinite(Number(progress)) ? Number(progress) : 0;
  elements.progressBar.style.width = `${Math.max(0, Math.min(100, safeProgress))}%`;
  elements.progressPercent.textContent = `${safeProgress}%`;
  elements.progressLabel.textContent = label || "等待运行";
}
function renderEvents(taskEvents) {
  if (!taskEvents.length) {
    elements.eventList.innerHTML = '<div class="empty-state">运行后将在这里显示节点事件</div>';
    return;
  }
  const grouped = new Map(nodes.map(([key, label]) => [key, { key, label, state: "idle" }]));
  taskEvents.forEach((event) => {
    if (!grouped.has(event.node_name)) {
      grouped.set(event.node_name, { key: event.node_name, label: event.node_name, state: "idle" });
    }
    const target = grouped.get(event.node_name);
    target.state = event.event_type;
    target.progress = event.progress;
    target.message = event.message;
    target.duration = event.duration_ms;
  });
  elements.eventList.innerHTML = [...grouped.values()]
    .map((item) => `
      <div class="event-row">
        <span class="event-marker ${item.state}"></span>
        <div class="event-copy">
          <strong>${escapeHtml(item.label)}</strong>
          <small>${escapeHtml(item.message || "等待执行")}</small>
        </div>
        <span class="event-value">${item.duration ? `${item.duration} ms` : item.state === "succeeded" ? "完成" : ""}</span>
      </div>`)
    .join("");
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
