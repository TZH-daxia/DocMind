import { fetchProjects } from "../api.js";

/**
 * 「项目」下拉（委托客户横栏中段）。
 *
 * 形态与 poOrder 订单新增页一致：点开就是**纯项目名列表**，面板里不放搜索框、
 * 不放编码、不放说明文字；没有有效委托客户时按钮直接不可点（poOrder 也要先选完
 * 委托客户才能选项目），状态原因放在按钮的 title 里。
 *
 * 取值口径：候选按委托客户收敛（`api/PubCustom?comxz=-1` 里 `fid == 当前客户`），
 * 列表显示项目简称，提交的是项目 ID；编码同样是选中后随行带出（拼单号会用到），
 * 只是不显示在列表里 —— 与 poOrder 一致（它的 el-option 只绑 usr_name）。
 *
 * 站点约束不在这里做 —— poOrder 也是选中之后才校验（「该项目没有 X 站点权限！」），
 * 那一步要等站点权限/身份落地。
 */
export const ProjectSelect = {
  name: "ProjectSelect",
  props: {
    modelValue: { type: String, default: "" },
    // 当前委托客户 ID：没有它就没有候选
    customerId: { type: String, default: "" },
    // 触发按钮上显示的文字（已选项目简称，或占位）
    display: { type: String, default: "请选择" },
    locked: { type: Boolean, default: false },
  },
  emits: ["change"],
  data() {
    return { open: false, items: [] };
  },
  computed: {
    // 客户 ID 是数字；委托客户输入框允许"手输未选"，那时存的是名称，不能拿来查项目
    customerPicked() {
      return /^\d+$/.test(String(this.customerId || "").trim());
    },
    disabled() {
      return this.locked || !this.customerPicked;
    },
    triggerTitle() {
      if (this.locked) {
        return "已提交，不可修改";
      }
      if (!this.customerId) {
        return "请先选择委托客户（项目按客户归属）";
      }
      if (!this.customerPicked) {
        return "请先从下拉候选里选中委托客户（项目按客户 ID 关联）";
      }
      return this.modelValue
        ? `已选项目 ID：${this.modelValue}`
        : "选择该委托客户下的项目（提交的是项目 ID）";
    },
  },
  watch: {
    // 换委托客户：候选作废（父组件已同时清空 gid/wtxmname），随后按新客户重新取候选
    customerId: {
      async handler() {
        this.items = [];
        this.open = false;
        if (!this.customerPicked) {
          return;
        }
        await this.load();
        this.autoSelectSingle();
      },
    },
  },
  mounted() {
    document.addEventListener("mousedown", this.onDocumentMouseDown);
    // 打开预览页时委托客户可能已由抽取/草稿填好（不触发上面的 watch）：
    // 这里补一次，保证"只有一个项目"的客户同样自动带出
    if (this.customerPicked && !this.modelValue) {
      this.load().then(() => this.autoSelectSingle());
    }
  },
  beforeUnmount() {
    document.removeEventListener("mousedown", this.onDocumentMouseDown);
  },
  methods: {
    // 点组件之外任意位置收起（事件冒泡到 document 才处理，点面板内不会误关）
    onDocumentMouseDown(event) {
      if (this.open && !this.$el.contains(event.target)) {
        this.open = false;
      }
    },
    async toggle() {
      if (this.disabled) {
        return;
      }
      this.open = !this.open;
      if (this.open) {
        await this.load();
      }
    },
    async load() {
      try {
        const payload = await fetchProjects(this.customerId);
        this.items = payload.items || [];
      } catch {
        // 接口没通（如服务未重启时 404）或主数据不可用：候选留空，不打断填写
        this.items = [];
      }
    },
    autoSelectSingle() {
      // 与 poOrder 一致：该委托客户只有一个项目时直接填入，不用再点下拉
      //（newFormCmpt.vue:4218-4231 的 wtxmOptions.length == 1 分支）
      if (this.items.length === 1 && this.items[0].id !== this.modelValue) {
        this.$emit("change", this.items[0]);
      }
    },
    select(item) {
      this.open = false;
      this.$emit("change", item);
    },
  },
  template: `
    <div class="doc-project-select">
      <button
        class="doc-dialog-customer-bar-project"
        type="button"
        :disabled="disabled"
        :title="triggerTitle"
        @click="toggle"
      >
        <span class="doc-dialog-customer-bar-key">项目</span>
        <span class="doc-dialog-customer-bar-value">{{ display }}</span>
        <span class="doc-dialog-customer-bar-caret" aria-hidden="true"></span>
      </button>
      <div v-if="open" class="doc-dialog-combobox-menu doc-project-menu">
        <button
          v-for="item in items"
          :key="item.id"
          class="doc-dialog-combobox-option"
          :class="{ 'is-active': item.id === modelValue }"
          type="button"
          @mousedown.prevent="select(item)"
        >
          <span class="doc-dialog-combobox-name">{{ item.name }}</span>
        </button>
        <p v-if="!items.length" class="doc-project-empty">无数据</p>
      </div>
    </div>
  `,
};
