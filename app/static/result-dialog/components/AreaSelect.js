/**
 * 「委托唯凯站点」下拉。
 *
 * 面板对齐 poOrder 的站点选择（src/components/smallTemplate/areaSelect.vue）：
 * 顶部一行「站点选择」标题，下面把站点按分组分栏排布 —— 分组按「先上下、再左右」
 * 的列优先顺序铺两行（第 1 行放第 0、2、4… 组，第 2 行放第 1、3、5… 组），
 * 每组是「分组名 + 若干站点」。
 *
 * 文案与取值都跟 poOrder 一致：显示字典原文（如「上海丨SHA」），进提交报文的
 * value 是站点中文名（areaSelect 的 valuetype 默认取 1，见其 cities 计算属性）。
 *
 * 触发胶囊沿用工具条样式（.doc-order-chip），宽度仍由「可能出现的所有文案」撑定
 * （同 ToolbarSelect），避免选前选后把右边的胶囊推来推去。候选未接入时（groups
 * 为空）面板给出说明，且胶囊仍显示订单上下文带来的原值，不会退回占位。
 */
export const AreaSelect = {
  name: "AreaSelect",
  props: {
    modelValue: { type: String, default: "" },
    // [{ label, options: [{ value, label }] }]，来自站点字典（groupid == 101）
    groups: { type: Array, default: () => [] },
    // 未选中时显示的字段名，如「唯凯站点」
    placeholder: { type: String, default: "" },
    title: { type: String, default: "" },
    disabled: { type: Boolean, default: false },
  },
  emits: ["update:modelValue"],
  data() {
    return { open: false };
  },
  computed: {
    flatOptions() {
      return this.groups.flatMap((group) => group.options || []);
    },
    selected() {
      return this.flatOptions.find((item) => item.value === this.modelValue) || null;
    },
    display() {
      if (!this.modelValue) {
        return this.placeholder;
      }
      // 取值不在字典里（订单上下文带来的站点可能已停用）时直接显示原值，
      // 否则操作员会以为这一项没值
      return this.selected ? this.selected.label : this.modelValue;
    },
    // 参与撑宽度的文案：占位 + 全部站点文案 + 当前取值（去重是为了 v-for 的 key 唯一）
    sizerTexts() {
      const texts = [
        this.placeholder,
        ...this.flatOptions.map((item) => item.label),
        this.modelValue,
      ];
      return [...new Set(texts.filter(Boolean))];
    },
  },
  mounted() {
    document.addEventListener("mousedown", this.onDocumentMouseDown);
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
    toggle() {
      if (this.disabled) {
        return;
      }
      this.open = !this.open;
    },
    select(item) {
      this.open = false;
      if (item.value !== this.modelValue) {
        this.$emit("update:modelValue", item.value);
      }
    },
  },
  template: `
    <div class="doc-order-chip" :class="{ 'is-set': modelValue }">
      <button
        class="doc-order-chip-trigger"
        type="button"
        :disabled="disabled"
        :title="title"
        @click="toggle"
        @keydown.esc="open = false"
      >
        <span class="doc-order-chip-label">{{ display }}</span>
        <span class="doc-order-chip-sizer" aria-hidden="true">
          <span v-for="text in sizerTexts" :key="text">{{ text }}</span>
        </span>
        <i class="doc-order-chip-caret" aria-hidden="true"></i>
      </button>
      <div v-if="open" class="doc-dialog-combobox-menu doc-order-menu doc-site-menu">
        <p class="doc-site-menu-title">站点选择</p>
        <div v-if="groups.length" class="doc-site-menu-grid">
          <div v-for="group in groups" :key="group.label" class="doc-site-menu-group">
            <p v-if="group.label" class="doc-site-menu-group-title">{{ group.label }}</p>
            <button
              v-for="item in group.options"
              :key="item.value"
              class="doc-site-menu-option"
              :class="{ 'is-active': item.value === modelValue }"
              type="button"
              @mousedown.prevent="select(item)"
            >{{ item.label }}</button>
          </div>
        </div>
        <p v-else class="doc-site-menu-empty">站点候选未接入</p>
      </div>
    </div>
  `,
};
