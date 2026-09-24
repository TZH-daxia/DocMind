import { fetchProjects } from "../api.js";
import { derivedSystem } from "../orderSystem.js";
import { dialogState } from "../state.js";

/**
 * 「项目」下拉（委托客户横栏中段）。
 *
 * 形态与 poOrder 订单新增页一致：点开就是**纯项目名列表**，面板里不放搜索框、
 * 不放编码、不放说明文字；没有有效委托客户时按钮直接不可点（poOrder 也要先选完
 * 委托客户才能选项目），状态原因放在按钮的 title 里。
 *
 * 取值口径：候选只按委托客户收敛（`usr_status` / `comxz` / `customxz` 由服务端过滤），
 * **不按站点过滤** —— 这一点必须和 poOrder 一致：它的站点校验发生在选中之后
 *
 *     // newOrderAdd.vue 的 beforeUpdate
 *     if (area && !data[0].area.split(',').includes(area) && data[0].area != '-1') {
 *       this.$message.error(`该项目没有${area}站点权限！`); this.inputModelData.area = "";
 *     }
 *
 * 候选如果先用站点过滤一遍，没有权限的项目会被直接滤掉、`gid` 也不会被写回，
 * 上面这段校验就永远触发不了——表现就是「选了一个没有该站点权限的客户，
 * 站点却毫无反应」。所以这里保留全部候选，由 `verifySelection()` 在选中
 * （含"只有一个项目"时的自动选中）之后判定：不通过就提示 + **清空站点**，
 * 与 poOrder 的处理完全相同。
 *
 * 另外还多做了**系统权限**（poOrder 同文件 1490-1501 的 `disabledSystemOption`）：
 * 项目允许的系统由服务端用 groupid == 57 的字典解析成名字（`systems`），
 * 当前「服务方式 + 运输种类」算出的 system 不在其中就提示「该项目没有X系统权限！」。
 * 这一条 poOrder 是"拦住这次切换"（不让改系统，什么也不清）；我们没有那个拦截点，
 * 改成清掉项目、保留站点与系统。
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
    // 站点权限用：工具条当前选中的唯凯站点
    area() {
      return String(dialogState.order.area || "");
    },
    // 系统权限用：服务方式 + 运输种类 派生（规则见 orderSystem.js）
    system() {
      return derivedSystem(dialogState.order);
    },
    selectedItem() {
      return this.items.find((item) => item.id === this.modelValue) || null;
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
        // 等父组件把 gid 写回来再校验：只有一个项目时它会被自动选中，这时应当走
        // 「该项目没有X站点权限！」（poOrder 的原话），而不是下面客户级的那句提示
        await this.$nextTick();
        this.verifySelection();
      },
    },
    // 换站点：候选与站点无关（同 poOrder，不做过滤），但要重判站点权限
    area() {
      this.verifySelection();
    },
    // 项目被选中 / 被清空：与 poOrder 的 beforeUpdate 一样即时校验
    modelValue() {
      this.verifySelection();
    },
    // 换业务系统：候选不变，但已选项目的系统权限可能不再成立
    system() {
      this.verifySelection();
    },
  },
  mounted() {
    document.addEventListener("mousedown", this.onDocumentMouseDown);
    // 打开预览页时委托客户可能已由抽取/草稿填好（不触发上面的 watch）：
    // 这里补一次，保证"只有一个项目"的客户同样自动带出，并校验已选项的权限
    if (this.customerPicked) {
      this.load().then(async () => {
        if (!this.modelValue) {
          this.autoSelectSingle();
          await this.$nextTick();
        }
        this.verifySelection();
      });
    }
  },
  beforeUnmount() {
    document.removeEventListener("mousedown", this.onDocumentMouseDown);
  },
  methods: {
    notify(message) {
      document.dispatchEvent(
        new CustomEvent("docmind:toast", { detail: { message, type: "error" } }),
      );
    },
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
        // 不带站点：候选是"该客户的全部项目"，站点权限由 verifySelection 判定
        const payload = await fetchProjects(this.customerId, "");
        this.items = payload.items || [];
      } catch {
        // 接口没通（如服务未重启时 404）或主数据不可用：候选留空，不打断填写
        this.items = [];
      }
    },
    // poOrder 的判定：项目 `area == '-1'` 表示不限站点，否则逗号分隔里要含当前站点
    allowsArea(item) {
      const raw = String(item?.area || "").trim();
      if (!raw || raw === "-1") {
        // 主数据缺字段时按"不限"处理（与后端 `_resolve_systems` 的放宽口径一致），
        // 免得主数据缺字段就把操作员拦死
        return true;
      }
      return raw
        .split(",")
        .map((part) => part.trim())
        .includes(this.area);
    },
    verifySelection() {
      const selected = this.selectedItem;
      // ① 已选项目 + 当前站点：与 poOrder 的 beforeUpdate 完全一致
      //   （写了 gid 就查它有没有这个站点；没有就提示并**清空站点**）
      if (this.area && selected && !this.allowsArea(selected)) {
        this.notify(`该项目没有${this.area}站点权限！`);
        this.clearArea();
        return;
      }
      // ② 还没选项目（含"该客户没有项目"以外的情况）：若该客户的候选**一个都不允许**
      //   当前站点，先告知并清空站点——否则操作员会在一个注定选不出项目的站点上白填。
      //   站点清空后会重新判定，而那时 this.area 为空、不会再命中，不会成环
      if (
        this.area &&
        this.items.length &&
        this.items.every((item) => !this.allowsArea(item))
      ) {
        this.notify(`该客户没有${this.area}站点权限的项目，请重新选择站点`);
        this.clearArea();
        return;
      }
      if (!selected) {
        // 已选项目不在该客户的候选里（主数据变更）：poOrder 的 beforeUpdate 要求
        // 恰好命中一条才校验，命中不到时什么也不做，这里同样保持不动
        return;
      }
      // ③ 系统权限：当前 system 不在项目允许的系统里
      const systems = selected.systems || [];
      if (
        systems.length &&
        !systems.includes("-1") &&
        !systems.includes(this.system)
      ) {
        // 这一条 poOrder 是"拦住这次切换"（不让改系统，什么也不清）；我们没有那个
        // 拦截点，改成清掉项目、保留站点与系统
        this.notify(`该项目没有${this.system}系统权限！`);
        this.clear();
      }
    },
    clearArea() {
      // 清空唯凯站点：与 poOrder 的 `this.inputModelData.area = ""` 一致。
      // 站点清掉后站点校验自然不再命中（本组件不会因此循环触发提示）
      this.open = false;
      dialogState.order.area = "";
    },
    clear() {
      // 清空项目：父组件会同时把 gid / wtxmname / wtxmcode 清掉
      this.open = false;
      this.$emit("change", { id: "", name: "", code: "" });
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
