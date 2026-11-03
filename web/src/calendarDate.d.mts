export type CalendarViewMode = "month" | "week" | "day";
export function shiftCalendarDate(date: Date, mode: CalendarViewMode, direction: number): Date;
export function formatCalendarDate(date: Date): string;
