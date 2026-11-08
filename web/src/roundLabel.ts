import type { GlobalTaskQueueItem } from "./api";

/**
 * 轮次类任务的业务文案：显示后端解析的 round_number（业务编号，「第 N 轮」），
 * 不使用 aggregate_id 里的数据库主键。轮次已删除或账号组不可读时 round_number 为 null，
 * 此时只显示不带编号的文案。
 */
export function roundTaskLabel(task: Pick<GlobalTaskQueueItem, "aggregate_label">): string {
  return task.aggregate_label || "轮次";
}
