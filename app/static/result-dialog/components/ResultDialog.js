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
    onSelectServices() {
      // 服务项目面板（服务代码的增删改）尚未接入：先明确告知，
      // 避免按下去没反应让人以为按钮坏了
      notify("服务项目面板尚未接入", "error");
    },
    async onSubmit() {
      if (this.state.submitted || this.state.submitting) {
        // 按钮已置灰，这里再兜一层：键盘/脚本触发也不允许重复提交
        return;
      }
      // 本地必填校验 + 真实提交（后端组装报文并调用 poOrder 的 api/ExHpoAxpline）
      const outcome = await resultDialog.submit();
      if (!outcome.ok) {
        if (outcome.message) {
          // 接口给出的原因，或条件必填没满足（如「项目」）：都带定位信息，
          // 把焦点带到出问题的那一行
          notify(outcome.message, "error");
          this.focusRow(outcome.firstError);
          return;
        }
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
      // 提交成功：订舱编号已由 resultDialog.submit 写进 state.orderCode（头部叉号左侧），
      // 这里只提示，不自动收起弹窗——方便立刻看到编号与页签变绿，也便于继续核对其他文件。
      // 文案按需求文档的返回格式『新增成功，订舱编号BOAE…』：poOrder 原文里还夹着
      // 信控提示，那段信息已经在「委托客户」下方单独展示，不重复塞进这条提示
      notify(
        outcome.orderCode ? `新增成功，订舱编号${outcome.orderCode}` : "新增成功",
        "success",
      );
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
          <!-- 收起：设计稿改为右上角的圆形叉号，只负责关闭弹窗；提交已移到弹窗底部 -->
          <div class="doc-dialog-head-actions">
            <!-- 订舱编号：提交成功后 poOrder 返回的编号，放在叉号左侧
                 （原先放工具条的编号槽，17 位编号会把右侧四个胶囊挤出去造成遮挡） -->
            <!-- 注意：本组件的 template 整体就是一层反引号字符串，这里**不能**再用
                 模板字符串（内层反引号会把外层提前截断，整个模块直接加载失败） -->
            <span
              v-if="state.orderCode"
              class="doc-dialog-order-code"
              :title="'订舱编号 ' + state.orderCode"
            >
              <i class="doc-dialog-order-code-dot" aria-hidden="true"></i>
              <span class="doc-dialog-order-code-text">订舱编号 {{ state.orderCode }}</span>
            </span>
            <button
              class="doc-dialog-collapse"
              type="button"
              aria-label="收起"
              title="收起"
              @click="onCollapse"
            ><i class="doc-dialog-collapse-icon" aria-hidden="true"></i></button>
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
            :order="state.order"
            :site-groups="state.siteGroups"
            :original="state.original"
            :raw-values="state.rawValues"
            :evidences="state.evidences"
            :locations="state.locations"
            :port-candidates="state.portCandidates"
            :errors="state.errors"
            :disabled="formDisabled"
            :locked="state.submitted"
            :reset-key="state.taskId"
            @field-focus="onFieldFocus"
            @select-services="onSelectServices"
          />
        </div>
        <!-- 提交移到弹窗底部（具体位置待设计确认，先靠右） -->
        <footer class="doc-dialog-foot">
          <!-- 已提交是终态：按钮沿用「提交订单」文案，仅置灰且不可再点 -->
          <button
            class="doc-dialog-submit"
            type="button"
            :disabled="state.submitted || state.submitting || formDisabled"
            @click="onSubmit"
          ><span v-if="state.submitting" class="doc-dialog-spinner" aria-hidden="true"></span>提交订单</button>
        </footer>
      </section>
    </div>
  `,
};
