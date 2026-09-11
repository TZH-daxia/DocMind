import {
  WEEKDAY_LABELS,
  buildMonthCells,
  parseIsoDate,
  shiftMonth,
  todayIso,
} from "../date.js";

const POPUP_WIDTH = 268;
const POPUP_HEIGHT = 332;
const EDGE_GAP = 8;

export const DatePicker = {
  name: "DatePicker",
  props: {
    value: { type: String, default: "" },
    anchor: { type: Object, default: null },
  },
  emits: ["select", "close"],
  data() {
    const parsed = parseIsoDate(this.value);
    const fallback = parsed || parseIsoDate(todayIso());
    return {
      viewYear: fallback.year,
      viewMonth: fallback.month,
    };
  },
  computed: {
    weekdays() {
      return WEEKDAY_LABELS;
    },
    cells() {
      return buildMonthCells(
        this.viewYear,
        this.viewMonth,
        this.value,
        todayIso(),
      );
    },
    title() {
      return `${this.viewYear} 年 ${this.viewMonth} 月`;
    },
    popupStyle() {
      const anchor = this.anchor;
      if (!anchor) {
        return { visibility: "hidden" };
      }
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const left = Math.min(
        Math.max(EDGE_GAP, anchor.left),
        Math.max(EDGE_GAP, viewportWidth - POPUP_WIDTH - EDGE_GAP),
      );
      const openUpward = anchor.bottom + POPUP_HEIGHT + EDGE_GAP > viewportHeight;
      const top = openUpward
        ? Math.max(EDGE_GAP, anchor.top - POPUP_HEIGHT - 6)
        : anchor.bottom + 6;
      return {
        left: `${left}px`,
        top: `${top}px`,
        width: `${POPUP_WIDTH}px`,
      };
    },
  },
  mounted() {
    document.addEventListener("keydown", this.onKeydown);
    window.addEventListener("scroll", this.onViewportChange, true);
    window.addEventListener("resize", this.onViewportChange);
  },
  beforeUnmount() {
    document.removeEventListener("keydown", this.onKeydown);
    window.removeEventListener("scroll", this.onViewportChange, true);
    window.removeEventListener("resize", this.onViewportChange);
  },
  methods: {
    onKeydown(event) {
      if (event.key === "Escape") {
        this.$emit("close");
      }
    },
    onViewportChange() {
      // 弹层用 fixed 定位，容器滚动后锚点会漂移，直接收起避免错位
      this.$emit("close");
    },
    shift(delta) {
      const { year, month } = shiftMonth(this.viewYear, this.viewMonth, delta);
      this.viewYear = year;
      this.viewMonth = month;
    },
    pick(iso) {
      this.$emit("select", iso);
    },
    pickToday() {
      this.$emit("select", todayIso());
    },
  },
  template: `
    <div class="doc-date-picker-layer">
      <div class="doc-date-picker-backdrop" @click="$emit('close')"></div>
      <div class="doc-date-picker" :style="popupStyle" role="dialog" aria-label="选择日期">
        <header class="doc-date-picker-head">
          <button type="button" class="doc-date-picker-nav" title="上一年" @click="shift(-12)">«</button>
          <button type="button" class="doc-date-picker-nav" title="上一月" @click="shift(-1)">‹</button>
          <strong>{{ title }}</strong>
          <button type="button" class="doc-date-picker-nav" title="下一月" @click="shift(1)">›</button>
          <button type="button" class="doc-date-picker-nav" title="下一年" @click="shift(12)">»</button>
        </header>
        <div class="doc-date-picker-weekdays">
          <span v-for="label in weekdays" :key="label">{{ label }}</span>
        </div>
        <div class="doc-date-picker-grid">
          <button
            v-for="cell in cells"
            :key="cell.iso"
            type="button"
            class="doc-date-picker-day"
            :class="{ 'is-outside': cell.outside, 'is-today': cell.today, 'is-selected': cell.selected }"
            @click="pick(cell.iso)"
          >{{ cell.day }}</button>
        </div>
        <footer class="doc-date-picker-foot">
          <button type="button" class="doc-date-picker-today" @click="pickToday">今天</button>
          <button type="button" class="doc-date-picker-close" @click="$emit('close')">收起</button>
        </footer>
      </div>
    </div>
  `,
};
