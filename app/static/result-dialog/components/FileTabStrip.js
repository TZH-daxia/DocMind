export const FileTabStrip = {
  name: "FileTabStrip",
  props: {
    // [{ taskId, name, submitted }]：顺序即展示顺序（与左侧文件列表一致）
    // submitted 为 true 表示该文件已提交成功，用绿色 + 对号区分
    files: { type: Array, default: () => [] },
    activeTaskId: { type: String, default: "" },
  },
  emits: ["select"],
  template: `
    <div v-if="files.length" class="doc-file-tabs" role="tablist" aria-label="最近的文件">
      <button
        v-for="file in files"
        :key="file.taskId"
        class="doc-file-tab"
        type="button"
        role="tab"
        :class="{ 'is-active': file.taskId === activeTaskId, 'is-submitted': file.submitted }"
        :aria-selected="file.taskId === activeTaskId"
        :title="file.name + (file.submitted ? '（已提交）' : '')"
        @click="$emit('select', file.taskId)"
      >
        <span v-if="file.submitted" class="doc-file-tab-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10.5"></circle>
            <path d="M7.2 12.3l3.3 3.3 6.3-6.6"></path>
          </svg>
        </span>
        <span class="doc-file-tab-name">{{ file.name }}</span>
      </button>
    </div>
  `,
};
