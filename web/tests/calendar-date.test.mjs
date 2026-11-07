import test from "node:test";
import assert from "node:assert/strict";
import { businessCalendarDate, formatBusinessDateTimeInput, formatCalendarDate, parseBusinessCalendarDate, shiftCalendarDate } from "../src/calendarDate.mjs";
import { formatBusinessDateTime, formatBusinessRelativeDateTime } from "../src/businessTime.mjs";

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

test("API timestamps render in fixed Asia/Shanghai time regardless of browser timezone", () => {
  const previousTz = process.env.TZ;
  process.env.TZ = "America/Los_Angeles";
  try {
    assert.equal(formatBusinessDateTime("2026-11-05T00:30:00Z"), "2026-11-05 08:30:00");
    assert.equal(formatBusinessDateTime("2026-11-04T16:30:00+00:00"), "2026-11-05 00:30:00");
    assert.equal(formatBusinessDateTime(null), "—");
    assert.equal(formatBusinessDateTime("legacy-invalid"), "legacy-invalid");
  } finally {
    if (previousTz === undefined) delete process.env.TZ;
    else process.env.TZ = previousTz;
  }
});


test("calendar day follows Asia/Shanghai near midnight regardless of browser timezone", () => {
  const tokyoBeforeShanghaiMidnight = new Date("2026-11-04T16:30:00.000Z");
  assert.equal(formatCalendarDate(businessCalendarDate(tokyoBeforeShanghaiMidnight)), "2026-11-05");
});

test("naive API calendar timestamps remain Shanghai wall time", () => {
  assert.equal(formatCalendarDate(parseBusinessCalendarDate("2026-11-05 00:30:00")), "2026-11-05");
});

test("offset-aware API timestamps are grouped by their Shanghai business date", () => {
  assert.equal(formatCalendarDate(parseBusinessCalendarDate("2026-11-04T16:30:00Z")), "2026-11-05");
  assert.equal(formatCalendarDate(parseBusinessCalendarDate("2026-11-05T00:30:00+09:00")), "2026-11-04");
});


test("datetime-local editing uses Shanghai wall time for offset-aware API timestamps", () => {
  assert.equal(formatBusinessDateTimeInput("2026-11-04T16:30:00Z"), "2026-11-05T00:30");
  assert.equal(formatBusinessDateTimeInput("2026-11-05 09:15:00"), "2026-11-05T09:15");
});

test("relative topic timestamps use Shanghai business day for naive API values", () => {
  const previousTz = process.env.TZ;
  process.env.TZ = "Asia/Tokyo";
  try {
    const now = new Date("2026-11-05T00:30:00+08:00");
    assert.equal(formatBusinessRelativeDateTime("2026-11-05 00:20:00", now), "今天 00:20");
    assert.equal(formatBusinessRelativeDateTime("2026-11-04 23:50:00", now), "11-04 23:50");
  } finally {
    if (previousTz === undefined) delete process.env.TZ;
    else process.env.TZ = previousTz;
  }
});

test("relative topic timestamps convert offset-aware values to Shanghai business time", () => {
  const now = new Date("2026-11-05T00:30:00+08:00");
  assert.equal(formatBusinessRelativeDateTime("2026-11-04T16:20:00Z", now), "今天 00:20");
  assert.equal(formatBusinessRelativeDateTime("2026-11-04T15:50:00Z", now), "11-04 23:50");
  assert.equal(formatBusinessRelativeDateTime("not-a-date", now), "not-a-date");
  assert.equal(formatBusinessRelativeDateTime(null, now), "—");
});
