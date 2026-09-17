import { FIELD_LABELS, REQUIRED_FIELDS } from "../constants.js";
import { buildFieldRows } from "../fields.js";
import { resultDialog } from "../composables/useResultDialog.js";
import { dialogState } from "../state.js";
import { DocumentPreview } from "./DocumentPreview.js";
import { FieldFormPanel } from "./FieldFormPanel.js";
import { FileTabStrip } from "./FileTabStrip.js";

function notify(message, type) {
  document.dispatchEvent(
    new CustomEvent("docmind:toast", { detail: { message, type } }),
  );
}

export const ResultDialog = {
  name: "ResultDialog",
  components: { DocumentPreview, FieldFormPanel, FileTabStrip },
  data() {
    return { state: dialogState };
  },
  computed: {
    fields() {
      return buildFieldRows(dialogState.form, dialogState.fieldStatus);
    },
    formDisabled() {
      return Boolean(dialogState.error);
    },
  },
  methods: {
    onCollapse() {
      resultDialog.close();
    },
    onSwitchTask(taskId) {
      resultDialog.switchTask(taskId);
    },
    onFieldFocus(payload) {
      resultDialog.focusField(payload);
    },
    onSubmit() {
      const outcome = resultDialog.submit();
      if (!outcome.ok) {
        const missing = REQUIRED_FIELDS.filter((key) => outcome.errors[key]);
        notify(
          missing.length > 3
            ? `共 ${missing.length} 项必填字段缺失，请补充后再提交`
            : `请先填写：${missing.map((key) => FIELD_LABELS[key]).join("、")}`,
          "error",
        );
        this.focusRow(outcome.firstError);
        return;
      }
      // 必填校验通过即视为提交成功（委托客户与港口已从主数据下拉选取，值本身有效）。
      // 注意：真实提交接口尚未接入，这里先给出「提交成功」提示并走页签颜色逻辑，
      // 目的是验证"提交成功→浅绿 / 选中→深绿 + 对号"的状态变化。
      notify("提交成功", "success");
      // 标记为已提交：页签条对该文件显示绿色 + 对号
      // （真实提交接口接入后，把这一句挪到提交成功回调里即可）
      resultDialog.markSubmitted(this.state.taskId);
      // 提交后不自动收起弹窗：方便立刻看到页签变绿，也便于继续核对其他文件
    },
    focusRow(fieldKey) {
      if (!fieldKey) {
        return;
      }
      this.$nextTick(() => {
        const row = document.getElementById(`doc-dialog-row-${fieldKey}`);
        if (!row) {
          return;
        }
        row.scrollIntoView({ block: "center", behavior: "smooth" });
        row.querySelector("input, textarea")?.focus();
      });
    },
  },
  template: `
    <div v-if="state.visible" class="doc-dialog-mask">
      <section class="doc-dialog" role="dialog" aria-modal="true" aria-label="分析结果">
        <header class="doc-dialog-head">
          <div class="doc-dialog-head-main">
            <div class="doc-dialog-head-title">
              <span class="doc-dialog-eyebrow">RESULT REVIEW</span>
              <span class="doc-dialog-task">{{ state.taskId }}</span>
            </div>
            <FileTabStrip
              :files="state.fileTabs"
              :active-task-id="state.taskId"
              @select="onSwitchTask"
            />
            <!-- 列表拿不到时退回显示当前文件名，避免头部没有文件标识 -->
            <h1 v-if="!state.fileTabs.length">{{ state.fileName || "分析结果" }}</h1>
          </div>
          <!-- 收起与提交是弹窗级操作：放在右侧，并在整块列头里上下居中 -->
          <div class="doc-dialog-head-actions">
            <button
              class="doc-dialog-collapse"
              type="button"
              @click="onCollapse"
            >收起</button>
            <button
              class="doc-dialog-submit"
              type="button"
              :disabled="state.submitting || formDisabled"
              @click="onSubmit"
            ><span v-if="state.submitting" class="doc-dialog-spinner" aria-hidden="true"></span>提交</button>
          </div>
        </header>
        <div class="doc-dialog-body">
          <DocumentPreview
            :page-urls="state.pageUrls"
            :loading="state.loading"
            :error="state.error"
            :highlight-boxes="state.highlightBoxes"
            :highlight-status="state.highlightStatus"
            :highlight-key="state.focusedLocationKey"
          />
          <FieldFormPanel
            :rows="fields"
            :form="state.form"
            :original="state.original"
            :raw-values="state.rawValues"
            :evidences="state.evidences"
            :locations="state.locations"
            :port-candidates="state.portCandidates"
            :errors="state.errors"
            :disabled="formDisabled"
            :reset-key="state.taskId"
            @field-focus="onFieldFocus"
          />
        </div>
      </section>
    </div>
  `,
};
