/** Format UTC/offset-aware API timestamps in the fixed Asia/Shanghai business timezone. */
const BUSINESS_TIME_ZONE = "Asia/Shanghai";

function businessParts(date) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: BUSINESS_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
}

function partValue(parts, type) {
  return parts.find((item) => item.type === type)?.value ?? "";
}

export function formatBusinessDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const parts = businessParts(date);
  return `${partValue(parts, "year")}-${partValue(parts, "month")}-${partValue(parts, "day")} ${partValue(parts, "hour")}:${partValue(parts, "minute")}:${partValue(parts, "second")}`;
}

/** Format topic collection timestamps as business-calendar relative labels.
 * Naive API timestamps are legacy Asia/Shanghai wall-clock values; timestamps
 * with an explicit zone are instants and are converted to Asia/Shanghai.
 */
export function formatBusinessRelativeDateTime(value, now = new Date()) {
  if (!value) return "—";
  const raw = String(value).trim();
  const naive = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/.exec(raw);
  let dateParts;
  if (naive) {
    dateParts = { year: naive[1], month: naive[2], day: naive[3], hour: naive[4], minute: naive[5] };
  } else {
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return raw;
    const parts = businessParts(date);
    dateParts = Object.fromEntries(["year", "month", "day", "hour", "minute"].map((key) => [key, partValue(parts, key)]));
  }
  const nowParts = businessParts(now);
  const sameDay = dateParts.year === partValue(nowParts, "year")
    && dateParts.month === partValue(nowParts, "month")
    && dateParts.day === partValue(nowParts, "day");
  return sameDay
    ? `今天 ${dateParts.hour}:${dateParts.minute}`
    : `${dateParts.month}-${dateParts.day} ${dateParts.hour}:${dateParts.minute}`;
}
