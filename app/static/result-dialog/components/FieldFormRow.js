import { CUSTOMER_COMBOBOX, PORT_COMBOBOX } from "../comboboxAdapters.js";
import { FIELD_STATUS_LABELS } from "../constants.js";
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
    locations: { type: Object, default: () => ({}) },
    portCandidates: { type: Object, default: () => ({}) },
    error: { type: [Boolean, String], default: false },
  },
  emits: ["field-focus"],
  data() {
    return {
      pickerOpen: false,
      pickerAnchor: null,
      candidatesExpanded: false,
    };
  },
  watch: {
    // 候选变化（切换任务/重新校验）时收起展开状态
    candidates() {
      this.candidatesExpanded = false;
    },
  },
  methods: {
    onDateClick() {
      this.$emit("field-focus", this.focusPayload);
      this.togglePicker();
    },
    onFieldFocus() {
      this.$emit("field-focus", this.focusPayload);
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
    badge() {
      if (this.row.control === "computed") {
        return "";
      }
      if (this.dirty) {
        return "已修改";
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
          :class="{ 'is-open': pickerOpen }"
          @click="onDateClick"
        >
          <span class="doc-dialog-date-text" :class="{ 'is-empty': !target }">
            {{ target || "YYYY-MM-DD" }}
          </span>
          <span class="doc-dialog-date-caret" aria-hidden="true"></span>
        </div>
        <textarea
          v-else-if="row.control === 'textarea' || row.multiline"
          class="doc-dialog-input"
          rows="2"
          v-model="target"
          @focus="onFieldFocus"
        ></textarea>
        <input
          v-else-if="row.control === 'number' || row.control === 'integer'"
          class="doc-dialog-input"
          type="text"
          :inputmode="row.control === 'integer' ? 'numeric' : 'decimal'"
          autocomplete="off"
          :value="target"
          @input="onNumericInput"
          @focus="onFieldFocus"
        >
        <SearchCombobox
          v-else-if="comboboxAdapter"
          :model-value="target"
          :adapter="comboboxAdapter"
          @update:model-value="selectComboboxValue"
          @focus="onFieldFocus"
        />
        <input
          v-else
          class="doc-dialog-input"
          type="text"
          v-model="target"
          @focus="onFieldFocus"
        >
        <small v-if="rawHint" class="doc-dialog-hint">{{ rawHint }}</small>
        <div v-if="candidates.length" class="doc-dialog-candidates">
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
