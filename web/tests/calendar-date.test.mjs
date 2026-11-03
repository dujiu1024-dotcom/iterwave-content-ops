import test from "node:test";
import assert from "node:assert/strict";
import { formatCalendarDate, shiftCalendarDate } from "../src/calendarDate.mjs";

test("month navigation clamps to the first day and crosses year boundaries", () => {
  const date = new Date(2026, 0, 31, 12, 30);
  assert.equal(formatCalendarDate(shiftCalendarDate(date, "month", -1)), "2025-12-01");
  assert.equal(formatCalendarDate(shiftCalendarDate(date, "month", 1)), "2026-02-01");
});

test("week and day navigation preserve local wall-clock time", () => {
  const date = new Date(2026, 10, 3, 9, 45);
  const week = shiftCalendarDate(date, "week", 1);
  const day = shiftCalendarDate(date, "day", -1);
  assert.equal(formatCalendarDate(week), "2026-11-10");
  assert.equal(formatCalendarDate(day), "2026-11-02");
  assert.equal(week.getHours(), 9);
  assert.equal(week.getMinutes(), 45);
});

test("week navigation crosses a daylight-saving boundary without changing wall time", () => {
  const previousTz = process.env.TZ;
  process.env.TZ = "America/New_York";
  try {
    const beforeDst = new Date(2026, 2, 7, 9, 30);
    const after = shiftCalendarDate(beforeDst, "week", 1);
    assert.equal(formatCalendarDate(after), "2026-03-14");
    assert.equal(after.getHours(), 9);
    assert.equal(after.getMinutes(), 30);
  } finally {
    if (previousTz === undefined) delete process.env.TZ;
    else process.env.TZ = previousTz;
  }
});

test("invalid date, mode, and zero direction are rejected", () => {
  assert.throws(() => formatCalendarDate(new Date("invalid")), TypeError);
  assert.throws(() => shiftCalendarDate(new Date(), "quarter", 1), TypeError);
  assert.throws(() => shiftCalendarDate(new Date(), "week", 0), TypeError);
});
