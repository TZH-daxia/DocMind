const analysisApi = {
  // 与后端 Settings.api_prefix + router 前缀保持一致（见 app/config.py）
  baseUrl: "/docmind/analysis",

  async listFiles() {
    return request(`${this.baseUrl}/files`);
  },

  async uploadFile(file, context) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("request_id", `ui-${Date.now()}-${crypto.randomUUID()}`);
    formData.append("schema_version", "po_order.v1");
    formData.append("context", JSON.stringify(context));
    formData.append("auto_start", "true");
    return request(`${this.baseUrl}/tasks`, {
      method: "POST",
      body: formData,
    });
  },

  async getEvents(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/events/history`);
  },

  async getResult(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/result`);
  },

  async getTask(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}`);
  },

  // 取消在途任务：任务立即停止且不可恢复（重跑需要重新上传）
  async cancelTask(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
    });
  },

  // 暂停在途任务：已产出的节点保留，可继续
  async pauseTask(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/pause`, {
      method: "POST",
    });
  },

  // 从暂停处继续：只跑没有产出结果的节点，已完成的步骤不重跑
  async resumeTask(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/resume`, {
      method: "POST",
    });
  },

  subscribe(taskId, onMessage, onError) {
    const source = new EventSource(
      `${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/events`,
    );
    source.onmessage = (message) => {
      try {
        onMessage(JSON.parse(message.data));
      } catch {
        onError(new Error("事件数据格式错误"));
      }
    };
    source.onerror = () => onError(new Error("进度连接已断开"));
    return source;
  },
};

async function request(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败：${response.status}`);
  }
  return payload;
}
