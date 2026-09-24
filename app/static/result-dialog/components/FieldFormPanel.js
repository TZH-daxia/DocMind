import { FieldFormRow } from "./FieldFormRow.js";
import { OrderToolbar } from "./OrderToolbar.js";

export const FieldFormPanel = {
  name: "FieldFormPanel",
  components: { FieldFormRow, OrderToolbar },
  props: {
    rows: { type: Array, default: () => [] },
    form: { type: Object, required: true },
    // 订单级上下文，供列头的订单工具条读写
    order: { type: Object, required: true },
    // 唯凯站点候选（按分组），供工具条「委托唯凯站点」下拉
    siteGroups: { type: Array, default: () => [] },
    original: { type: Object, default: () => ({}) },
    rawValues: { type: Object, default: () => ({}) },
    evidences: { type: Object, default: () => ({}) },
    locations: { type: Object, default: () => ({}) },
    portCandidates: { type: Object, default: () => ({}) },
    errors: { type: Object, default: () => ({}) },
    disabled: { type: Boolean, default: false },
    // 已提交：整表字段只读（与 disabled 的区别是仍可点击核对原文高亮）
    locked: { type: Boolean, default: false },
    // 数据来源标识（任务 ID）：变化时强制重建各行，避免输入框内部状态
    // （下拉选中项、搜索词、展开态等）残留到下一个任务
    resetKey: { type: [String, Number], default: "" },
  },
  // 收起/提交已移到弹窗右上角与底部，本组件只上报字段聚焦与服务项目点击
  emits: ["field-focus", "select-services"],
  template: `
    <section class="doc-dialog-form" :class="{ 'is-disabled': disabled }">
      <!-- 列头改为订单工具条：编号 + 站点/服务方式/运输种类/订舱操作 + 服务项目 -->
      <header class="doc-dialog-form-head is-toolbar">
        <OrderToolbar
          :order="order"
          :site-groups="siteGroups"
          :locked="locked"
          @select-services="$emit('select-services')"
        />
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
              :locked="locked"
              @field-focus="$emit('field-focus', $event)"
            />
          </tbody>
        </table>
      </div>
    </section>
  `,
};
