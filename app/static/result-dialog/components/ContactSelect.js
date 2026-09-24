import { fetchContacts } from "../api.js";
import { derivedSystem } from "../orderSystem.js";
import { dialogState } from "../state.js";

/**
 * 「本票客户客服联系人」选择器（委托客户横栏右端）。
 *
 * 口径与 poOrder 一致（`src/components/templates/customerRel.vue` 的 `getCustomerRelData`）：
 * - 候选 = `GET api/CustomerRel/GetCustomerRel`（BoManagementWebApi）+ `post`/`lxrtitle`/
 *   `department` 都是「客服」，只保留 `comxz == '1'` 的有效联系人（服务端已过滤）；
 * - **本票默认联系人** = `defaultlxrjson` 里同时匹配当前站点与业务系统的那条
 *   （`system == -1` 通配）；有默认就自动带出，否则取第一条——与 poOrder 列表里
 *   点第一条带出等价；
 * - 选中结果通过 `change` 交给父组件写回 `form.customerRelList`：横栏按钮上的
 *   数量、以及提交报文里 `customerRelList[0].name/mobile/phone` 都由它驱动。
 *
 * 交互与「项目」下拉同构（见 ProjectSelect）：按钮 + 向左展开的纯姓名列表，
 * 点组件之外收起；没有候选时按钮不可点，原因写在 title 里。
 */
export const ContactSelect = {
  name: "ContactSelect",
  props: {
    // 当前委托客户 ID：没有它就没有候选
    customerId: { type: String, default: "" },
    // 已选中的联系人（父组件的 form.customerRelList[0]），用于按钮文案与高亮
    selected: { type: Object, default: null },
    locked: { type: Boolean, default: false },
  },
  emits: ["change"],
  data() {
    return {
      open: false,
      items: [],
      // 是否查过（区分"还没查"与"查了但没有"）
      loaded: false,
      // 查询序号：切客户/换站点时丢弃过期响应
      seq: 0,
    };
  },
  computed: {
    // 客户 ID 是数字；委托客户输入框允许"手输未选"，那时存的是名称，不能拿来查联系人
    customerPicked() {
      return /^\d+$/.test(String(this.customerId || "").trim());
    },
    // 没有站点就没有「区域」这个维度：本票默认联系人判不出来
    areaReady() {
      return Boolean(this.area);
    },
    disabled() {
      // 没有站点时按钮**仍然可点**——点了会提示「请选择区域」（与 poOrder 一致），
      // 而不是给一个点不动的死按钮
      return (
        this.locked ||
        !this.customerPicked ||
        (this.areaReady && !this.items.length)
      );
    },
    display() {
      return this.selected
        ? `本票客户客服联系人（1）`
        : "本票客户客服联系人";
    },
    triggerTitle() {
      if (this.locked) {
        return "已提交，不可修改";
      }
      if (!this.customerPicked) {
        return "请先选择委托客户（联系人与客户绑定）";
      }
      if (!this.areaReady) {
        return "请先选择区域（本票联系人按区域取默认）";
      }
      if (!this.items.length) {
        return this.loaded
          ? "该委托客户没有维护客服联系人"
          : "客服联系人获取中…";
      }
      return this.selected?.name
        ? `当前：${this.selected.name}（点击可更换）`
        : "选择本票客户客服联系人";
    },
    // 信控/联系人都按站点，取工具条当前选中的唯凯站点
    area() {
      return String(dialogState.order.area || "");
    },
    // 业务系统（如「空出」）：规则见 orderSystem.js，与后端 compute_system 同一口径
    system() {
      return derivedSystem(dialogState.order);
    },
  },
  watch: {
    // 换委托客户：候选作废（父组件已同时清空 customerRelList），按新客户重取
    customerId: {
      async handler() {
        this.open = false;
        this.items = [];
        this.loaded = false;
        await this.load();
      },
    },
    // 站点/业务系统变了，「本票默认联系人」也要重新判定
    area() {
      this.load();
    },
    system() {
      this.load();
    },
  },
  mounted() {
    document.addEventListener("mousedown", this.onDocumentMouseDown);
    // 打开预览页时客户可能已由抽取/草稿填好（不触发 watched 的 customerId 变化）
    if (this.customerPicked) {
      this.load();
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
    notify(message) {
      document.dispatchEvent(
        new CustomEvent("docmind:toast", { detail: { message, type: "error" } }),
      );
    },
    toggle() {
      if (this.locked) {
        return;
      }
      // 与 poOrder 的 customerRelDialogVisible 一致：没有区域就不开面板，只提示
      if (!this.areaReady) {
        this.notify("请选择区域");
        return;
      }
      if (this.disabled) {
        return;
      }
      this.open = !this.open;
    },
    async load() {
      const fid = String(this.customerId || "").trim();
      const seq = (this.seq += 1);
      if (!this.customerPicked || !this.areaReady) {
        // 没客户 / 没站点都不查：poOrder 的 getCustomerRelData 开头也是 `if (!area) return`
        this.items = [];
        this.loaded = false;
        return;
      }
      try {
        const payload = await fetchContacts(fid, this.area, this.system);
        if (seq !== this.seq) {
          return;
        }
        this.items = payload.items || [];
      } catch {
        // 接口没通（如服务未重启时 404）或主数据不可用：候选留空，不打断填写
        if (seq !== this.seq) {
          return;
        }
        this.items = [];
      }
      this.loaded = true;
      this.autoSelect();
    },
    autoSelect() {
      // 与 poOrder 一致（customerRel.vue 的 handleCustomerRelData）：只认带了
      // defaultlxr 标记的那一条，**一条都没有就留空**、由人工在列表里选——
      // poOrder 那里是 `filter(item => item.defaultlxr)` 后直接 emit，
      // 不会退化成"取第一条"。用户已经选过的不覆盖
      if (this.selected || !this.items.length) {
        return;
      }
      const preferred = this.items.find((item) => item.is_default);
      if (preferred) {
        this.$emit("change", preferred);
      }
    },
    select(item) {
      this.open = false;
      this.$emit("change", item);
    },
  },
  template: `
    <div class="doc-contact-select">
      <button
        class="doc-dialog-customer-bar-rel doc-contact-trigger"
        type="button"
        :disabled="disabled"
        :title="triggerTitle"
        @click="toggle"
      >
        <span>{{ display }}</span>
        <span class="doc-dialog-customer-bar-caret" aria-hidden="true"></span>
      </button>
      <div v-if="open" class="doc-dialog-combobox-menu doc-contact-menu">
        <button
          v-for="(item, index) in items"
          :key="item.name + '-' + index"
          class="doc-dialog-combobox-option"
          :class="{ 'is-active': selected && selected.name === item.name && selected.mobile === item.mobile }"
          type="button"
          :title="[item.name, item.mobile || item.phone, item.email].filter(Boolean).join(' · ')"
          @mousedown.prevent="select(item)"
        >
          <span class="doc-dialog-combobox-name">{{ item.name || "（无姓名）" }}</span>
        </button>
        <p v-if="!items.length" class="doc-contact-empty">无数据</p>
      </div>
    </div>
  `,
};
