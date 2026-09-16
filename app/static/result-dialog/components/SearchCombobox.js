// 输入防抖：避免每次按键都打后端
const SEARCH_DEBOUNCE_MS = 250;
// 输入关键字的最短长度：单字符会命中一大片，没有参考价值
const MIN_KEYWORD_LENGTH = 2;

/**
 * 通用的"输入即搜索"下拉输入框。
 *
 * 与具体业务解耦：候选是什么、选项怎么显示、选中回填什么值，全部由
 * adapter 描述（见 comboboxAdapters.js），因此委托客户与始发/目的港共用同一
 * 套交互与降级逻辑。
 *
 * adapter 字段：
 * - noun                名词，用于拼装提示文案（"客户" / "港口"）
 * - search(keyword)     搜索接口，返回 { enabled, items }
 * - toValue(item)       选中后写入表单的值
 * - toPrimary(item)     候选项主文案，也是选中后输入框的回显内容
 * - toMeta(item)        候选项副文案（可选）
 * - toDisabled(item)    候选项是否不可选（可选，默认都可选）
 * - disabledMeta        不可选时显示的短标记（可选）
 * - disabledTitle       不可选时的悬停说明（可选）
 * - placeholder         正常态占位文案
 * - degradedPlaceholder 主数据不可用时的占位文案
 * - selectedTitle(item) 选中后的悬停说明（可选）
 * - pendingTitle        有输入但未选中时的悬停说明（可选）
 */
export const SearchCombobox = {
  name: "SearchCombobox",
  props: {
    modelValue: { type: [String, Number], default: "" },
    adapter: { type: Object, required: true },
  },
  emits: ["update:modelValue", "focus"],
  data() {
    return {
      // 输入框当前文本：选中后是回显文案（客户名 / 三字码），输入过程中是搜索词
      keyword: "",
      options: [],
      open: false,
      loading: false,
      // 主数据不可用（未配置或请求失败）：退化为手工填写
      degraded: false,
      searchError: "",
      selected: null,
      activeIndex: -1,
      timer: null,
      requestSeq: 0,
      // 最近一次由本组件发出的值：用于忽略自身 update 触发的回灌。
      // 初值必须是 null 而非 ""：否则"切换到该字段为空的任务"时，
      // 新值 "" 会被误判成自身回灌，跳过同步，旧值就留在输入框里了
      lastEmitted: null,
      // 用户是否已与本组件交互过：交互后不再接受载入期的反查结果
      touched: false,
      // 输入框是否处于聚焦态：失焦后到达的搜索结果不回弹下拉
      focused: false,
    };
  },
  mounted() {
    this.syncFromValue(this.modelValue);
  },
  beforeUnmount() {
    clearTimeout(this.timer);
  },
  watch: {
    modelValue(value) {
      // 只忽略"本组件自己发出去的值"回灌（lastEmitted 为 null 表示尚未发过）
      if (this.lastEmitted !== null && String(value ?? "") === this.lastEmitted) {
        return;
      }
      // 外部改动（加载结果、切换任务）以新值为准：
      // 重置交互、降级与候选状态后重新同步，避免上一个任务的残留
      this.touched = false;
      this.degraded = false;
      this.searchError = "";
      this.options = [];
      this.open = false;
      this.syncFromValue(value);
    },
  },
  computed: {
    emptyHint() {
      if (this.searchError) {
        return this.searchError;
      }
      if (!this.canSearch(this.keyword)) {
        return `继续输入以搜索${this.adapter.noun}（至少 ${MIN_KEYWORD_LENGTH} 个字符）`;
      }
      return `没有匹配的${this.adapter.noun}`;
    },
    // 输入框里有内容但不是"下拉选中"的结果（手输未选 / 抽取值对不上主数据）：
    // 用于给出"待确认"的边框提示
    needsPick() {
      return !this.degraded && Boolean(this.keyword.trim()) && !this.selected;
    },
    // 全部提示只挂在 title 上：不占版面，也不会改变输入框/行高
    inputHint() {
      if (this.degraded) {
        return "";
      }
      if (this.selected) {
        return this.adapter.selectedTitle
          ? this.adapter.selectedTitle(this.selected) || ""
          : "";
      }
      if (this.keyword.trim()) {
        return this.adapter.pendingTitle || "";
      }
      return "";
    },
  },
  methods: {
    valueOf(item) {
      return String(this.adapter.toValue(item) ?? "");
    },
    primaryOf(item) {
      return String(this.adapter.toPrimary(item) ?? "");
    },
    metaOf(item) {
      return this.adapter.toMeta ? String(this.adapter.toMeta(item) ?? "") : "";
    },
    disabledOf(item) {
      return this.adapter.toDisabled ? Boolean(this.adapter.toDisabled(item)) : false;
    },
    emitValue(value) {
      this.lastEmitted = String(value ?? "");
      this.$emit("update:modelValue", value);
    },
    canSearch(text) {
      return String(text ?? "").trim().length >= MIN_KEYWORD_LENGTH;
    },
    async callSearch(keyword) {
      const seq = (this.requestSeq += 1);
      this.loading = true;
      try {
        const payload = await this.adapter.search(keyword);
        if (seq !== this.requestSeq) {
          return null;
        }
        return {
          enabled: payload.enabled !== false,
          items: Array.isArray(payload.items) ? payload.items : [],
        };
      } catch (error) {
        if (seq !== this.requestSeq) {
          return null;
        }
        return {
          enabled: false,
          items: [],
          message: `${this.adapter.noun}搜索失败：${error.message}`,
        };
      } finally {
        if (seq === this.requestSeq) {
          this.loading = false;
        }
      }
    },
    async syncFromValue(value) {
      if (this.touched) {
        return;
      }
      const text = String(value ?? "").trim();
      if (!text) {
        this.selected = null;
        this.keyword = "";
        return;
      }
      // 外部给的值可能是 ID/三字码（下拉回填）或名称（上下文/抽取）：
      // 反查一次，能对上就展示可读文案并标记为"已选"，对不上则保留原文展示
      // 但不标记已选（界面上会以"待确认"样式提示需要重新选择）
      const result = await this.callSearch(text);
      if (!result || this.touched) {
        return;
      }
      if (!result.enabled) {
        this.degraded = true;
        this.searchError = result.message || "";
        this.selected = null;
        this.keyword = text;
        return;
      }
      this.degraded = false;
      this.searchError = "";
      const hit = result.items.find((item) => this.valueOf(item) === text) || null;
      this.selected = hit;
      this.keyword = hit ? this.primaryOf(hit) : text;
    },
    async runSearch(text) {
      const result = await this.callSearch(text);
      if (!result) {
        return;
      }
      if (!result.enabled) {
        // 主数据不可用：退化为普通输入框，原样保留用户输入，不阻塞填写
        this.degraded = true;
        this.searchError = result.message || "";
        this.options = [];
        this.open = false;
        this.emitValue(this.keyword);
        return;
      }
      this.degraded = false;
      this.searchError = "";
      this.options = result.items;
      this.activeIndex = result.items.length ? 0 : -1;
      // 失焦后才返回的结果不弹下拉
      this.open = this.focused;
    },
    onFocus() {
      this.focused = true;
      this.$emit("focus");
      // 只有"当前关键字确实搜过且有候选"时才重开下拉：提交校验失败会自动
      // 聚焦到缺失字段，若只看 options 会把上一次搜索的残留候选弹出来
      if (!this.degraded && this.options.length && this.canSearch(this.keyword)) {
        this.open = true;
      }
    },
    onInput(event) {
      const text = event.target.value;
      this.keyword = text;
      this.touched = true;
      if (this.degraded) {
        // 退化为手工填写：输入即写入表单，保持原有可用性
        this.emitValue(text);
        return;
      }
      // 手输内容只用于搜索：只要不再是"已选项的回显"，表单值立即清空，
      // 最终写进表单的值必须由下拉选中产生
      if (!this.selected || this.primaryOf(this.selected) !== text) {
        this.selected = null;
        this.emitValue("");
      }
      clearTimeout(this.timer);
      if (!this.canSearch(text)) {
        this.options = [];
        this.open = false;
        this.activeIndex = -1;
        return;
      }
      this.timer = setTimeout(() => this.runSearch(text), SEARCH_DEBOUNCE_MS);
    },
    moveActive(step) {
      if (!this.options.length) {
        return;
      }
      this.open = true;
      const count = this.options.length;
      this.activeIndex = (this.activeIndex + step + count) % count;
    },
    onEnter() {
      if (!this.open || this.activeIndex < 0) {
        return;
      }
      this.selectOption(this.options[this.activeIndex]);
    },
    close() {
      this.open = false;
    },
    onBlur() {
      this.focused = false;
      // 失焦后不再触发本次防抖搜索
      clearTimeout(this.timer);
      // 候选项用 mousedown.prevent 保住焦点，这里延后关闭避免"点不到"
      setTimeout(() => {
        this.open = false;
      }, 0);
      // 没选中就等于没填：搜索词与残留候选一起清掉，
      // 既避免"看起来填了、实际值为空"，也避免下次聚焦弹出旧候选
      if (!this.selected && String(this.modelValue ?? "") === "") {
        this.keyword = "";
        this.options = [];
        this.activeIndex = -1;
      }
    },
    selectOption(item) {
      if (!item || this.disabledOf(item)) {
        // 不可选项（如已停用客户）不写入表单，避免把无效值带进订单
        return;
      }
      this.touched = true;
      this.selected = item;
      this.keyword = this.primaryOf(item);
      this.emitValue(this.valueOf(item));
      this.options = [];
      this.open = false;
      this.activeIndex = -1;
    },
  },
  template: `
    <div class="doc-dialog-combobox">
      <input
        class="doc-dialog-input"
        :class="{ 'is-pending': needsPick }"
        type="text"
        autocomplete="off"
        :value="keyword"
        :placeholder="degraded ? adapter.degradedPlaceholder : adapter.placeholder"
        :title="inputHint"
        @input="onInput"
        @focus="onFocus"
        @blur="onBlur"
        @keydown.down.prevent="moveActive(1)"
        @keydown.up.prevent="moveActive(-1)"
        @keydown.enter.prevent="onEnter"
        @keydown.esc="close"
      >
      <small v-if="degraded" class="doc-dialog-hint">
        {{ adapter.noun }}主数据不可用，已退化为手工填写{{ searchError ? "（" + searchError + "）" : "" }}
      </small>
      <div v-else-if="open" class="doc-dialog-combobox-menu">
        <div v-if="loading" class="doc-dialog-combobox-tip">搜索中…</div>
        <div v-else-if="!options.length" class="doc-dialog-combobox-tip">{{ emptyHint }}</div>
        <button
          v-for="(item, index) in options"
          :key="valueOf(item)"
          class="doc-dialog-combobox-option"
          :class="{ 'is-active': index === activeIndex, 'is-unavailable': disabledOf(item) }"
          type="button"
          :disabled="disabledOf(item)"
          :title="disabledOf(item) ? (adapter.disabledTitle || '') : metaOf(item)"
          @mousedown.prevent="selectOption(item)"
        >
          <span class="doc-dialog-combobox-name">{{ primaryOf(item) }}</span>
          <span v-if="metaOf(item)" class="doc-dialog-combobox-meta">{{ metaOf(item) }}</span>
          <span v-if="disabledOf(item) && adapter.disabledMeta" class="doc-dialog-combobox-meta">
            {{ adapter.disabledMeta }}
          </span>
        </button>
      </div>
    </div>
  `,
};
