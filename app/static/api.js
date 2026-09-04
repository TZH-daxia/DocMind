const analysisApi = {
  baseUrl: "/api/v1/analysis",

  async listFiles() {
    return request(`${this.baseUrl}/files`);
  },

  async uploadFile(file, context) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("request_id", `ui-${Date.now()}-${crypto.randomUUID()}`);
    formData.append("schema_version", "po_order.v1");
    formData.append("context", JSON.stringify(context));
    formData.append("auto_start", "false");
    return request(`${this.baseUrl}/tasks`, {
      method: "POST",
      body: formData,
    });
  },

  async runTask(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/run`, {
      method: "POST",
    });
  },

  async getStatus(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}`);
  },

  async getResult(taskId) {
    return request(`${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/result`);
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
