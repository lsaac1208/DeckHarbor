import type { DownloadTask } from "./api";

export function downloadFailed(task: DownloadTask): boolean {
  return task.status === "failed" || task.extract_status === "failed" || task.import_status === "failed";
}

export function downloadSucceeded(task: DownloadTask): boolean {
  if (downloadFailed(task) || task.status !== "complete") return false;
  if (task.game_id) return task.extract_status === "complete" && task.import_status === "complete";
  return !task.extract_status || task.extract_status === "complete";
}
