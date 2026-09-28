/**
 * 工具条上的下拉。
 *
 * 胶囊外观沿用工具条原有样式（32px、#F9FAFC 底 + #E5E7EB 边 + 4px 圆角），
 * 展开后的菜单**直接复用表单里搜索下拉的菜单类**（.doc-dialog-combobox-menu /
 * .doc-dialog-combobox-option），这样工具条和表单里的下拉长得完全一致。
 *
 * 为什么不用原生 <select>：它的弹出层由操作系统绘制，CSS 改不了样式，
 * 所以会出现"上边的下拉和下面的下拉不是一个东西"。
 *
 * options 是 [{ value, label }]：label 给人看，value 进提交报文，两者可能不同
 * （订舱操作的 label 是「唯凯配舱」，value 是「自货」）。
 *
 * 宽度稳定性：触发按钮里放了一份**隐身的最宽文案**（各选项文案，用 grid 叠在同一格，
 * 容器宽度即最宽者），当前值绝对定位叠在上面。于是胶囊宽度固定，在长度不同的取值之间
 * 切换时不会把右边的胶囊推来推去。
 *
 * 有值时**不再把占位（字段名，4 个字）算进宽度**：服务方式 / 运输种类 / 订舱操作都有
 * 默认值、取值只有两个字，算上字段名会把胶囊白撑宽一圈；只有值为空（理论上不该出现）
 * 时才靠它兜底。站点不走这套 —— 见 AreaSelect，它宽度自适应。
 */
export const ToolbarSelect = {
  name: "ToolbarSelect",
  props: {
    modelValue: { type: String, default: "" },
    options: { type: Array, default: () => [] },
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
    selected() {
      return this.options.find((item) => item.value === this.modelValue) || null;
    },
    display() {
      if (!this.modelValue) {
        return this.placeholder;
      }
      // 取值不在候选项里时（如唯凯站点候选尚未接入、但 context 已带来站点），
      // 直接显示原值而不是退回占位 —— 否则操作员会以为这一项没值
      return this.selected ? this.selected.label : this.modelValue;
    },
    // 参与撑宽度的文案：全部选项 + 当前取值；**有值时不算占位**（见文件头说明）。
    // 去重是为了 v-for 的 key 唯一（取值本身就是选项文案时会有重复）
    sizerTexts() {
      const texts = [
        ...(this.modelValue ? [] : [this.placeholder]),
        ...this.options.map((item) => item.label),
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
    // 点组件之外任意位置收起。注意事件是冒泡到 document 才处理的，
    // 所以点在菜单项上时（同样在 $el 内）不会误关
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
      <div v-if="open" class="doc-dialog-combobox-menu doc-order-menu">
        <button
          v-for="item in options"
          :key="item.value"
          class="doc-dialog-combobox-option"
          :class="{ 'is-active': item.value === modelValue }"
          type="button"
          @mousedown.prevent="select(item)"
        >
          <span class="doc-dialog-combobox-name">{{ item.label }}</span>
        </button>
      </div>
    </div>
  `,
};
