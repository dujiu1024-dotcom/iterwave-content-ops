/** Format UTC/offset-aware API timestamps in the fixed Asia/Shanghai business timezone. */
export function formatBusinessDateTime(value, assumeNaiveUtc = false) {
  if (!value) return "—";
  const raw = String(value).trim();\n  const hasZone = /(?:Z|[+-]\\d{2}:?\\d{2})$/i.test(raw);\n  const normalized = assumeNaiveUtc && !hasZone ? `${raw.replace(" ", "T")}Z` : raw;\n  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return String(value);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const part = (type) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")}`;
}
