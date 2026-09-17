import { FieldFormRow } from "./FieldFormRow.js";

export const FieldFormPanel = {
  name: "FieldFormPanel",
  components: { FieldFormRow },
  props: {
    rows: { type: Array, default: () => [] },
    form: { type: Object, required: true },
    original: { type: Object, default: () => ({}) },
    rawValues: { type: Object, default: () => ({}) },
    evidences: { type: Object, default: () => ({}) },
    locations: { type: Object, default: () => ({}) },
    portCandidates: { type: Object, default: () => ({}) },
    errors: { type: Object, default: () => ({}) },
    disabled: { type: Boolean, default: false },
    // 数据来源标识（任务 ID）：变化时强制重建各行，避免输入框内部状态
    // （下拉选中项、搜索词、展开态等）残留到下一个任务
    resetKey: { type: [String, Number], default: "" },
  },
  // 收起/提交已移到弹窗右上角，本组件只上报字段聚焦
  emits: ["field-focus"],
  template: `
    <section class="doc-dialog-form" :class="{ 'is-disabled': disabled }">
      <!-- 列头只留栏目名：标题文案去掉，收起/提交按钮在弹窗右上角 -->
      <header class="doc-dialog-form-head">
        <span class="doc-dialog-eyebrow">EXTRACTED FIELDS</span>
      </header>
      <div class="doc-dialog-table-wrap">
        <table class="doc-dialog-table">
          <thead>
            <tr><th scope="col">项目</th><th scope="col">内容</th></tr>
          </thead>
          <tbody>
            <FieldFormRow
              v-for="row in rows"
              :key="resetKey + '-' + row.id"
              :row="row"
              :form="form"
              :original="original"
              :raw-values="rawValues"
              :evidences="evidences"
              :locations="locations"
              :port-candidates="portCandidates"
              :error="errors[row.id]"
              @field-focus="$emit('field-focus', $event)"
            />
          </tbody>
        </table>
      </div>
    </section>
  `,
};
