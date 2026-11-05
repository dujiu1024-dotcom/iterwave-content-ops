/** Calendar navigation helpers. All values are local wall-clock dates, not UTC instants. */
export function shiftCalendarDate(date, mode, direction) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new TypeError("date must be a valid Date");
  }
  if (!Number.isInteger(direction) || direction === 0) {
    throw new TypeError("direction must be a non-zero integer");
  }
  if (mode === "month") {
    return new Date(date.getFullYear(), date.getMonth() + direction, 1, date.getHours(), date.getMinutes(), date.getSeconds(), date.getMilliseconds());
  }
  if (mode !== "week" && mode !== "day") {
    throw new TypeError("mode must be month, week, or day");
  }
  const next = new Date(date);
  next.setDate(next.getDate() + (mode === "week" ? 7 : 1) * direction);
  return next;
}

export function formatCalendarDate(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new TypeError("date must be a valid Date");
  }
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}


const BUSINESS_TIME_ZONE = "Asia/Shanghai";

/** Return the fixed business-calendar day as a local wall-date (not a UTC instant). */
export function businessCalendarDate(value = new Date()) {
  const date = value instanceof Date ? value : parseBusinessCalendarDate(value);
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new TypeError("value must be a valid date or timestamp");
  }
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: BUSINESS_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date);
  const part = (type) => parts.find((item) => item.type === type)?.value ?? "";
  return new Date(Number(part("year")), Number(part("month")) - 1, Number(part("day")), 12);
}

/** Parse API timestamps using the business calendar: naive values are legacy Shanghai wall time. */
export function parseBusinessCalendarDate(value) {
  if (value instanceof Date) return businessCalendarDate(value);
  const raw = String(value ?? "").trim();
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\\.(\d+))?)?)?$/.exec(raw);
  if (match) {
    const [, y, m, d, hh = "12", mm = "0", ss = "0"] = match;
    return new Date(Number(y), Number(m) - 1, Number(d), Number(hh), Number(mm), Number(ss));
  }
  const instant = new Date(raw);
  if (Number.isNaN(instant.getTime())) return null;
  return businessCalendarDate(instant);
}


/** Convert an API timestamp to the datetime-local wall clock used by the business calendar. */
export function formatBusinessDateTimeInput(value) {
  if (!value) return "";
  const raw = String(value).trim();
  const naive = /^(\\d{4}-\\d{2}-\\d{2})[ T](\\d{2}:\\d{2})/.exec(raw);
  if (naive && !/(?:Z|[+-]\\d{2}:?\\d{2})$/i.test(raw)) return `${naive[1]}T${naive[2]}`;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: BUSINESS_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const part = (type) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}
