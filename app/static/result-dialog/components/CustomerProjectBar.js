import { fetchCredit } from "../api.js";
import { CUSTOMER_COMBOBOX } from "../comboboxAdapters.js";
import { dialogState } from "../state.js";
import { ProjectSelect } from "./ProjectSelect.js";
import { SearchCombobox } from "./SearchCombobox.js";

/**
 * 委托客户 · 项目 · 本票客户客服联系人。
 *
 * 版式对齐设计稿：三段合并成一条圆角描边横栏，段间用竖线分隔，客户名占满
 * 剩余宽度，右端是本票客户客服联系人按钮。三者是一条业务链——选委托客户决定
 * 项目候选，项目和区域/系统又决定默认联系人，因此必须同栏展示，拆成三行会让人
 * 看不出从属关系。
 *
 * 客户名下方会带一行信用等级 / 信控提示（口径同 poOrder 订单新增页：等级来自
 * 客户主数据 `creditlevel`，提示来自 `api/PubCredit` 的 `resultmessage`），
 * 位置与样式复用「待审核原文」那一行（`.doc-dialog-hint`）。
 *
 * 这一行**必须跟着当前委托客户走**，有两个坑：
 * 1. 切任务时本组件会重新挂载（行组件按 resetKey 重建），但 `mounted` 那一刻新的
 *    表单还没加载进来——`form.fid` 仍是上一个任务的。所以不能只在 mounted 里查，
 *    必须 watch `form.fid`（新表单到位后补查、清空时立刻收起文案）。
 * 2. 查询是异步的：快速切客户/切任务时，先发的请求可能后返回。用自增序号
 *    （creditSeq）在写回前比对，过期响应直接丢弃。
 *
 * 项目段已接入（见 ProjectSelect）：候选按委托客户收敛，选中后回填
 * `gid`（提交值）与 `wtxmname` / `wtxmcode`（展示与拼单号）。
 *
 * 联系人主数据尚未接入（接口在 poOrder 的 BoManagementWebApi，需要鉴权）：
 * 按钮由 `relReady` 控制可用性。未就绪时**不改变外观**（设计稿里这段就是常态
 * 样式），只置为 disabled 并在 title 里写明缺哪个接口，避免用灰底伪装成
 * "已完成只读"。
 */
export const CustomerProjectBar = {
  name: "CustomerProjectBar",
  components: { ProjectSelect, SearchCombobox },
  props: {
    form: { type: Object, required: true },
    locked: { type: Boolean, default: false },
    // 联系人主数据就绪前，联系人按钮只展示占位、不响应点击
    relReady: { type: Boolean, default: false },
  },
  emits: ["focus", "select-rel"],
  data() {
    return {
      // 信用等级 / 信控提示（合成文案），无客户或查不到时为空
      creditHint: "",
      // 信控查询的请求序号：写回前比对，丢弃过期响应
      creditSeq: 0,
    };
  },
  computed: {
    adapter() {
      return CUSTOMER_COMBOBOX;
    },
    customerValue() {
      return this.form.fid ?? "";
    },
    projectValue() {
      return this.form.gid ?? "";
    },
    // 项目名来自项目主数据；未选中时显示占位文案
    projectText() {
      if (!this.projectValue) {
        return "请选择";
      }
      return String(this.form.wtxmname || this.projectValue);
    },
    // 信控按站点分别校验，取工具条当前选中的唯凯站点
    currentArea() {
      return String(dialogState.order.area || "");
    },
    relCount() {
      return Array.isArray(this.form.customerRelList)
        ? this.form.customerRelList.length
        : 0;
    },
    relText() {
      return this.relCount
        ? `本票客户客服联系人（${this.relCount}）`
        : "本票客户客服联系人";
    },
    relTitle() {
      if (!this.relReady) {
        return "联系人主数据尚未接入：需按 fid 查 api/CustomerRel/GetCustomerRel";
      }
      return this.relCount ? "修改本票客户客服联系人" : "选择本票客户客服联系人";
    },
  },
  watch: {
    // 表单对象被整体替换（切任务 / 重新解析后 applyResult 会赋值一个新对象）：
    // 此刻它已经是新任务的值，用它去查才准。不能改成在 mounted 里查——行组件是按
    // resetKey 重建的，mounted 早于新表单到位，那时 form 还是上一个任务的
    form() {
      this.loadCreditHint();
    },
    // 委托客户被改（下拉选中、被清空）：重新取信控提示
    "form.fid"() {
      this.loadCreditHint();
    },
    // 换站点也要重取：信控是「客户 + 站点」维度的（poOrder 查询时也带 area）
    currentArea() {
      this.loadCreditHint();
    },
  },
  methods: {
    async loadCreditHint() {
      // 序号自增：本次请求返回时若已有更新的请求（或已切任务），就不再写回
      const seq = (this.creditSeq += 1);
      // 只有从下拉里真正选中的客户才有信控可查（fid 是数字 ID；
      // 手输未选时字段里是名称，查不到任何东西）——此时必须清空，
      // 否则会留下上一个客户的文案
      const fid = String(this.form.fid || "").trim();
      if (!/^\d+$/.test(fid)) {
        this.creditHint = "";
        return;
      }
      try {
        const payload = await fetchCredit(fid, this.currentArea);
        if (seq !== this.creditSeq) {
          return;
        }
        this.creditHint = payload.hint || "";
      } catch {
        // 信控接口不可用或查询失败：不显示这一行，不阻断填写
        if (seq !== this.creditSeq) {
          return;
        }
        this.creditHint = "";
      }
    },
    onCustomerChange(value) {
      // 换委托客户会连带换掉项目候选和联系人候选，先把从属值清空，
      // 否则会留下"客户已换、项目还是上一家"的脏数据。
      // 注意用直接赋值：本项目是 Vue 3，没有 Vue 2 的 this.$set
      //（调用它会抛 TypeError，赋值整段都不执行 —— 曾因此导致 fid 永远写不进去）
      this.form.fid = value;
      this.form.gid = "";
      this.form.wtxmname = "";
      this.form.wtxmcode = "";
      this.form.customerRelList = [];
      // 值没变时 watch 不会触发（例如切任务后重选同一个客户），这里补一次；
      // 序号守卫会让重复请求里较早的那个自动作废
      this.loadCreditHint();
    },
    onProjectChange(item) {
      // 项目 ID 进提交报文；名称与编码随行带出（编码用于拼单号）
      this.form.gid = item.id;
      this.form.wtxmname = item.name || "";
      this.form.wtxmcode = item.code || "";
    },
    onRelClick() {
      if (this.locked || !this.relReady) {
        return;
      }
      this.$emit("select-rel");
    },
  },
  template: `
    <div class="doc-dialog-customer-field">
      <div class="doc-dialog-customer-bar">
        <div class="doc-dialog-customer-bar-main">
          <SearchCombobox
            :model-value="customerValue"
            :adapter="adapter"
            :locked="locked"
            @update:model-value="onCustomerChange"
            @focus="$emit('focus')"
          />
        </div>
        <span class="doc-dialog-customer-bar-divider" aria-hidden="true"></span>
        <ProjectSelect
          :model-value="projectValue"
          :customer-id="customerValue"
          :display="projectText"
          :locked="locked"
          @change="onProjectChange"
        />
        <button
          class="doc-dialog-customer-bar-rel"
          type="button"
          :disabled="locked || !relReady"
          :title="relTitle"
          @click="onRelClick"
        >{{ relText }}</button>
      </div>
      <small v-if="creditHint" class="doc-dialog-hint">{{ creditHint }}</small>
    </div>
  `,
};
