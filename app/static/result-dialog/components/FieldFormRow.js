import { FIELD_STATUS_LABELS } from "../constants.js";
import { DatePicker } from "./DatePicker.js";

export const FieldFormRow = {
  name: "FieldFormRow",
  components: { DatePicker },
  props: {
    row: { type: Object, required: true },
    form: { type: Object, required: true },
    original: { type: Object, default: () => ({}) },
    rawValues: { type: Object, default: () => ({}) },
    locations: { type: Object, default: () => ({}) },
    error: { type: Boolean, default: false },
  },
  emits: ["field-focus"],
  data() {
    return {
      pickerOpen: false,
      pickerAnchor: null,
    };
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
        <input
          v-else
          class="doc-dialog-input"
          type="text"
          v-model="target"
          @focus="onFieldFocus"
        >
        <small v-if="rawHint" class="doc-dialog-hint">{{ rawHint }}</small>
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
