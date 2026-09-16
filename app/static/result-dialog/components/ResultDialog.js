import { FIELD_LABELS, REQUIRED_FIELDS } from "../constants.js";
import { buildFieldRows } from "../fields.js";
import { resultDialog } from "../composables/useResultDialog.js";
import { dialogState } from "../state.js";
import { DocumentPreview } from "./DocumentPreview.js";
import { FieldFormPanel } from "./FieldFormPanel.js";

function notify(message, type) {
  document.dispatchEvent(
    new CustomEvent("docmind:toast", { detail: { message, type } }),
  );
}

export const ResultDialog = {
  name: "ResultDialog",
  components: { DocumentPreview, FieldFormPanel },
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
      // 必填校验通过即收起弹窗；委托客户与港口已从主数据下拉选取，
      // 值本身即有效，无需再回后端校验（真实提交由后端后续接入）
      notify("校验通过", "success");
      resultDialog.close();
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
          <div>
            <span class="doc-dialog-eyebrow">RESULT REVIEW</span>
            <h1>{{ state.fileName || "分析结果" }}</h1>
          </div>
          <span class="doc-dialog-task">{{ state.taskId }}</span>
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
            :submitting="state.submitting"
            :disabled="formDisabled"
            :reset-key="state.taskId"
            @submit="onSubmit"
            @collapse="onCollapse"
            @field-focus="onFieldFocus"
          />
        </div>
      </section>
    </div>
  `,
};
