import { CUSTOMER_COMBOBOX, PORT_COMBOBOX } from "../comboboxAdapters.js";
import { FIELD_STATUS_LABELS, REVIEW_STATUSES } from "../constants.js";
import { DatePicker } from "./DatePicker.js";
import { SearchCombobox } from "./SearchCombobox.js";

// 候选默认展示数量：超出折叠，可展开全部
const CANDIDATE_PREVIEW_LIMIT = 6;

export const FieldFormRow = {
  name: "FieldFormRow",
  components: { DatePicker, SearchCombobox },
  props: {
    row: { type: Object, required: true },
    form: { type: Object, required: true },
    original: { type: Object, default: () => ({}) },
    rawValues: { type: Object, default: () => ({}) },
    evidences: { type: Object, default: () => ({}) },
    locations: { type: Object, default: () => ({}) },
    portCandidates: { type: Object, default: () => ({}) },
    error: { type: [Boolean, String], default: false },
    // 已提交：字段只读（仍可点击核对原文高亮，但不能再改动）
    locked: { type: Boolean, default: false },
  },
  emits: ["field-focus"],
  data() {
    return {
      pickerOpen: false,
      pickerAnchor: null,
      candidatesExpanded: false,
    };
  },
  mounted() {
    // 结果载入即按内容撑开多行输入框，不必等用户先点一下
    this.$nextTick(() => this.autoGrowAll());
  },
  watch: {
    // 候选变化（切换任务/重新校验）时收起展开状态
    candidates() {
      this.candidatesExpanded = false;
    },
    // 切换任务、后端回填等程序性改动后，多行输入框按新内容重新撑高
    target() {
      this.$nextTick(() => this.autoGrowAll());
    },
  },
  methods: {
    onDateClick() {
      this.$emit("field-focus", this.focusPayload);
      if (this.locked) {
        // 已提交：只做核对高亮，不打开日期选择器
        return;
      }
      this.togglePicker();
    },
    onFieldFocus() {
      this.$emit("field-focus", this.focusPayload);
    },
    onMultilineInput(event) {
      // 输入即撑高：内容多长就显示多长（高度算准就不会出现滚动条）
      this.autoGrowTextarea(event.target);
    },
    autoGrowTextarea(el) {
      if (!el) {
        return;
      }
      // 先复位再按内容设置：否则内容变短时高度收不回来
      el.style.height = "auto";
      // box-sizing: border-box 下 height 不含边框，补上边框像素避免出现 1px 滚动
      const border = el.offsetHeight - el.clientHeight;
      el.style.height = `${el.scrollHeight + border}px`;
    },
    autoGrowAll() {
      const refs = this.$refs.multilineInput;
      const nodes = Array.isArray(refs) ? refs : refs ? [refs] : [];
      nodes.forEach((el) => this.autoGrowTextarea(el));
    },
    togglePicker() {
      if (this.pickerOpen) {
        this.pickerOpen = false;
        return;
      }
      this.pickerAnchor = this.$refs.dateField?.getBoundingClientRect() ?? null;
      this.pickerOpen = true;
    },
    onSelectDate(iso) {
      this.target = iso;
      this.pickerOpen = false;
    },
    selectCandidate(item) {
      // 点选候选 = 人工填入三字码：走"已修改"流程，候选随值非空自动消失
      this.target = item.three_code;
    },
    selectComboboxValue(value) {
      // 下拉回填的值（委托客户 ID / 港口三字码）交给 target 的 setter 写回表单
      this.target = value;
    },
    sanitizeNumber(value) {
      const digits = String(value).replace(/[^\d.]/g, "");
      if (this.row.control === "integer") {
        return digits.replace(/\./g, "");
      }
      const [head, ...rest] = digits.split(".");
      return rest.length ? `${head}.${rest.join("")}` : head;
    },
    onNumericInput(event) {
      const raw = event.target.value;
      const sanitized = this.sanitizeNumber(raw);
      if (sanitized !== raw) {
        event.target.value = sanitized;
      }
      this.target = sanitized;
    },
  },
  computed: {
    // 需要"输入即搜索"下拉的控件：委托客户与始发/目的港，差别只在适配器
    comboboxAdapter() {
      if (this.row.control === "customer") {
        return CUSTOMER_COMBOBOX;
      }
      if (this.row.control === "port") {
        return PORT_COMBOBOX;
      }
      return null;
    },
    target: {
      get() {
        if (this.row.subKey) {
          return this.form[this.row.fieldKey]?.[this.row.subKey] ?? "";
        }
        return this.form[this.row.fieldKey] ?? "";
      },
      set(value) {
        if (this.row.subKey) {
          this.form[this.row.fieldKey][this.row.subKey] = value;
          return;
        }
        this.form[this.row.fieldKey] = value;
      },
    },
    originalValue() {
      if (this.row.subKey) {
        return this.original[this.row.fieldKey]?.[this.row.subKey] ?? "";
      }
      return this.original[this.row.fieldKey] ?? "";
    },
    dirty() {
      return JSON.stringify(this.target) !== JSON.stringify(this.originalValue);
    },
    rawHint() {
      const raw = this.rawValues[this.row.fieldKey];
      if (!raw) {
        return "";
      }
      return this.row.control === "date"
        ? `原文：${raw}（不是标准日期，请重新选择）`
        : `原文：${raw}（不是纯数字，请重新填写）`;
    },
    reviewHint() {
      // 待审核类字段：把文档原文显示在输入框下，人工核对时不必来回翻原件
      if (!REVIEW_STATUSES.includes(this.row.status)) {
        return "";
      }
      // 日期/数字控件已经用 rawHint 显示了不可渲染的原值，不重复
      if (this.rawHint) {
        return "";
      }
      const quotes = this.evidences[this.row.fieldKey] || [];
      if (!quotes.length) {
        return "";
      }
      // 冲突字段带上两处不一致的原文，正是需要人工判断的点
      const shown =
        quotes.length > 2
          ? `${quotes.slice(0, 2).join(" ／ ")} …`
          : quotes.join(" ／ ");
      const reason =
        this.row.status === "conflict"
          ? "（文档中该字段有多个不一致的值，请确认）"
          : "";
      return `原文：${shown}${reason}`;
    },
    reviewTitle() {
      return (this.evidences[this.row.fieldKey] || []).join(" ／ ");
    },
    badge() {
      if (this.row.control === "computed") {
        return "";
      }
      if (this.dirty) {
        return "已修改";
      }
      // 选填字段本来就可以为空，"缺失"不算问题：不标记，避免满屏「缺失」
      // （后端或模型也可能漏输出该字段，前端兜底同样会给成 missing）
      if (this.row.status === "missing" && !this.row.required) {
        return "";
      }
      return FIELD_STATUS_LABELS[this.row.status] || "";
    },
    badgeClass() {
      return this.dirty ? "edited" : this.row.status || "confirmed";
    },
    focusPayload() {
      return {
        locationKey: this.row.locationKey || "",
        fieldKey: this.row.fieldKey || "",
        status: this.row.status || "",
        // 空值行没有可核对的原文内容：不高亮、也不做区域回退
        hasValue: this.hasExtractedValue,
      };
    },
    candidates() {
      // 只有"没有值"的行才展示候选：归一化成功的字段已直接填入三字码
      if (!this.row.locationKey || String(this.target ?? "").trim()) {
        return [];
      }
      return this.portCandidates[this.row.fieldKey] || [];
    },
    // 模板无法访问模块常量，经 computed 暴露
    candidatePreviewLimit() {
      return CANDIDATE_PREVIEW_LIMIT;
    },
    visibleCandidates() {
      return this.candidatesExpanded
        ? this.candidates
        : this.candidates.slice(0, CANDIDATE_PREVIEW_LIMIT);
    },
    hiddenCandidatesCount() {
      return Math.max(0, this.candidates.length - CANDIDATE_PREVIEW_LIMIT);
    },
    errorText() {
      // 提交前校验的失败原因（后端逐字段下发），行内展示
      return typeof this.error === "string" ? this.error : "";
    },
    hasExtractedValue() {
      // 用"抽取出来的原值"判断，而不是当前输入框内容：人工补录的值本来
      // 就不在文档里，不该被当成"未定位"
      return String(this.originalValue ?? "").trim() !== "";
    },
    hasLocation() {
      if (!this.row.locationKey) {
        return false;
      }
      const own = this.locations[this.row.locationKey];
      const fallback = this.locations[this.row.fieldKey];
      return Boolean(own?.length || fallback?.length);
    },
    showMissingLocation() {
      // 只在"抽取到了值、但原文里定位不到、且人还没改过"时提示：
      // - 空值字段已有「缺失」标记，不需要再来一个「未定位」
      // - 人工补录/修改后是「已修改」，未定位的说明已失效
      return (
        Boolean(this.row.locationKey) &&
        this.hasExtractedValue &&
        !this.dirty &&
        !this.hasLocation
      );
    },
  },
  template: `
    <tr :id="'doc-dialog-row-' + row.id" :class="{ 'has-error': error }">
      <th scope="row" class="doc-dialog-field">
        <span class="doc-dialog-field-label">
          {{ row.label }}<i v-if="row.required" class="doc-dialog-required">*</i>
        </span>
        <span class="doc-dialog-markers">
          <span class="doc-dialog-marker-slot">
            <em v-if="badge" class="doc-dialog-badge" :class="badgeClass">{{ badge }}</em>
          </span>
          <span class="doc-dialog-marker-slot">
            <em
              v-if="showMissingLocation"
              class="doc-dialog-locate-chip"
              title="文档中没有找到这个值的位置，请人工核对"
            >未定位</em>
          </span>
        </span>
      </th>
      <td class="doc-dialog-value">
        <input
          v-if="row.control === 'computed'"
          class="doc-dialog-input is-computed"
          type="text"
          :value="row.value"
          readonly
          title="自动计算：实际毛重（公斤） × 预计运费单价（CNY）"
        >
        <div
          v-else-if="row.control === 'date'"
          ref="dateField"
          class="doc-dialog-date"
          :class="{ 'is-open': pickerOpen, 'is-locked': locked }"
          @click="onDateClick"
        >
          <span class="doc-dialog-date-text" :class="{ 'is-empty': !target }">
            {{ target || "YYYY-MM-DD" }}
          </span>
          <span class="doc-dialog-date-caret" aria-hidden="true"></span>
        </div>
        <textarea
          v-else-if="row.control === 'textarea' || row.multiline"
          ref="multilineInput"
          class="doc-dialog-input"
          rows="1"
          v-model="target"
          :readonly="locked"
          @input="onMultilineInput"
          @focus="onFieldFocus"
        ></textarea>
        <input
          v-else-if="row.control === 'number' || row.control === 'integer'"
          class="doc-dialog-input"
          type="text"
          :inputmode="row.control === 'integer' ? 'numeric' : 'decimal'"
          autocomplete="off"
          :value="target"
          :readonly="locked"
          @input="onNumericInput"
          @focus="onFieldFocus"
        >
        <SearchCombobox
          v-else-if="comboboxAdapter"
          :model-value="target"
          :adapter="comboboxAdapter"
          :locked="locked"
          @update:model-value="selectComboboxValue"
          @focus="onFieldFocus"
        />
        <input
          v-else
          class="doc-dialog-input"
          type="text"
          v-model="target"
          :readonly="locked"
          @focus="onFieldFocus"
        >
        <small v-if="rawHint" class="doc-dialog-hint">{{ rawHint }}</small>
        <small v-if="reviewHint" class="doc-dialog-hint" :title="reviewTitle">{{ reviewHint }}</small>
        <!-- 已提交：候选是补录辅助，锁定后不再展示 -->
        <div v-if="candidates.length && !locked" class="doc-dialog-candidates">
          <span class="doc-dialog-candidates-label">候选({{ candidates.length }})</span>
          <button
            v-for="item in visibleCandidates"
            :key="item.three_code"
            class="doc-dialog-candidate-chip"
            type="button"
            :title="item.english_name || item.three_code"
            @click="selectCandidate(item)"
          >{{ item.three_code }}<small v-if="item.english_name">{{ item.english_name }}</small></button>
          <button
            v-if="hiddenCandidatesCount > 0"
            class="doc-dialog-candidate-more"
            type="button"
            @click="candidatesExpanded = true"
          >展开其余 {{ hiddenCandidatesCount }} 个</button>
          <button
            v-else-if="candidates.length > candidatePreviewLimit"
            class="doc-dialog-candidate-more"
            type="button"
            @click="candidatesExpanded = false"
          >收起</button>
        </div>
        <small v-if="errorText" class="doc-dialog-error">{{ errorText }}</small>
        <DatePicker
          v-if="pickerOpen"
          :value="target"
          :anchor="pickerAnchor"
          @select="onSelectDate"
          @close="pickerOpen = false"
        />
      </td>
    </tr>
  `,
};
