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
