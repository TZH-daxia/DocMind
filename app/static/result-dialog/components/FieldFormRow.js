import { FIELD_STATUS_LABELS } from "../constants.js";

export const FieldFormRow = {
  name: "FieldFormRow",
  props: {
    row: { type: Object, required: true },
    form: { type: Object, required: true },
    original: { type: Object, default: () => ({}) },
    rawValues: { type: Object, default: () => ({}) },
    error: { type: Boolean, default: false },
  },
  methods: {
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
  },
  template: `
    <tr :id="'doc-dialog-row-' + row.id" :class="{ 'has-error': error }">
      <th scope="row" class="doc-dialog-field">
        <span class="doc-dialog-field-label">
          {{ row.label }}<i v-if="row.required" class="doc-dialog-required">*</i>
        </span>
        <em v-if="badge" class="doc-dialog-badge" :class="badgeClass">{{ badge }}</em>
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
        <template v-else-if="row.control === 'date'">
          <input class="doc-dialog-input" type="date" v-model="target">
        </template>
        <textarea
          v-else-if="row.control === 'textarea' || row.multiline"
          class="doc-dialog-input"
          rows="2"
          v-model="target"
        ></textarea>
        <input
          v-else-if="row.control === 'number' || row.control === 'integer'"
          class="doc-dialog-input"
          type="text"
          :inputmode="row.control === 'integer' ? 'numeric' : 'decimal'"
          autocomplete="off"
          :value="target"
          @input="onNumericInput"
        >
        <input v-else class="doc-dialog-input" type="text" v-model="target">
        <small v-if="rawHint" class="doc-dialog-hint">{{ rawHint }}</small>
      </td>
    </tr>
  `,
};
