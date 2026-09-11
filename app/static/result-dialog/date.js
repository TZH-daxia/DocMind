export const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

const ISO_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

export function toIsoDate(year, month, day) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${year}-${pad(month)}-${pad(day)}`;
}

export function parseIsoDate(text) {
  const match = ISO_PATTERN.exec(String(text ?? ""));
  if (!match) {
    return null;
  }
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(year, month - 1, day);
  if (
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) {
    return null;
  }
  return { year, month, day };
}

export function todayIso() {
  const now = new Date();
  return toIsoDate(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

export function shiftMonth(year, month, delta) {
  const total = year * 12 + (month - 1) + delta;
  return { year: Math.floor(total / 12), month: (total % 12) + 1 };
}

export function buildMonthCells(year, month, selectedIso, todayValue) {
  const firstDay = new Date(year, month - 1, 1);
  // 周一作为第一列，与中文日历习惯一致
  const leading = (firstDay.getDay() + 6) % 7;
  const cells = [];
  for (let index = 0; index < 42; index += 1) {
    const date = new Date(year, month - 1, 1 - leading + index);
    const iso = toIsoDate(date.getFullYear(), date.getMonth() + 1, date.getDate());
    cells.push({
      iso,
      day: date.getDate(),
      outside: date.getMonth() !== month - 1,
      today: iso === todayValue,
      selected: iso === selectedIso,
    });
  }
  return cells;
}
