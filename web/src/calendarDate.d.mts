export type CalendarViewMode = "month" | "week" | "day";
export function shiftCalendarDate(date: Date, mode: CalendarViewMode, direction: number): Date;
export function formatCalendarDate(date: Date): string;
export function businessCalendarDate(value?: Date | string): Date;
export function parseBusinessCalendarDate(value: Date | string): Date | null;
export function formatBusinessDateTimeInput(value: string | null): string;
