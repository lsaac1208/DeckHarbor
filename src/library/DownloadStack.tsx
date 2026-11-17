import { ActionButton } from "../ui";
import { downloadFailed, downloadSucceeded } from "../downloadState";
/* ═══════════════════════════════════════════
   NewFreedeck — 下载堆叠组件（Stack 动画 + 直操作）
   ═══════════════════════════════════════════ */

import { Focusable, showModal, ConfirmModal } from "@decky/ui";
import { useState } from "react";
import type { DownloadTask } from "../api";
import { getDownloadPoller } from "../downloadPoller";

interface DownloadStackProps {
  tasks: DownloadTask[];
}

const RECT_W = 60, RECT_H = 90, STROKE_W = 2.5, GAP = 4;
const RING_W = RECT_W + GAP * 2, RING_H = RECT_H + GAP * 2;
const PERIMETER = 2 * (RING_W + RING_H);
const SVG_W = RING_W + STROKE_W * 2, SVG_H = RING_H + STROKE_W * 2;

export function DownloadStack({ tasks }: DownloadStackProps) {
  const [expanded, setExpanded] = useState(false);
  if (!tasks.length) return null;
  const visible = expanded ? tasks : tasks.slice(0, 1);
  return <div className="nfd-downloads">
    <style>{CSS}</style>
    <h3>下载任务</h3>
    {visible.map(task => <DownloadCard key={task.task_id} task={task} />)}
    {tasks.length > 1 && <ActionButton onClick={() => setExpanded(value => !value)}>
      {expanded ? "收起任务列表" : `查看全部 ${tasks.length} 个任务`}
    </ActionButton>}
  </div>;
}

/* ── 单张下载卡片（含 Focusable）─────────── */

function DownloadCard({ task }: { task: DownloadTask }) {
  const isExtracting = (task.extract_status === "running" || task.extract_status === "queued");
  const isImporting = task.import_status === "need_shortcut" || task.import_status === "discovering" || task.import_status === "importing" || task.import_status === "applying";
  const isImportDone = task.import_status === "complete";
  const isPaused = task.status === "paused";
  const isFailed = downloadFailed(task);
  const isDone = downloadSucceeded(task);
  const isRunning = !isPaused && !isFailed && !isDone && task.status !== "complete";

  const pct = Math.min(100, Math.max(0,
    isExtracting ? (task.extract_progress || 0) : (task.progress || 0)
  ));
  const speed = isRunning && !isExtracting ? formatSpeed(task.speed || 0) : "";
  const name = task.game_title || task.file_name || "";
  const cover = (task as any).cover_url || "";

  const borderColor = isFailed ? "#c44" : isDone ? "#5a5" : isPaused ? "#f5a623" : isExtracting ? "#f5a623" : isImporting ? "#67c1f5" : "#369ff4";
  const dashOffset = PERIMETER * (1 - pct / 100);

  const handleA = () => {
    const poller = getDownloadPoller();
    if (isPaused) {
      poller.resumeTask(task.task_id);
    } else if (isRunning) {
      poller.pauseTask(task.task_id);
    }
  };

  const handleX = () => {
    if (isDone || isFailed) {
      void getDownloadPoller().cancelTask(task.task_id);
    } else {
      const poller = getDownloadPoller();
      // 弹窗确认是否同时删除已下载文件
      const close = showModal(
        <ConfirmModal
          strTitle="取消下载"
          strDescription={`确定取消 "${name}" 的下载吗？\n已下载的文件会保留。`}
          strOKButtonText="取消下载"
          strCancelButtonText="继续下载"
          onOK={() => {
            poller.cancelTask(task.task_id);
            close.Close();
          }}
          onCancel={() => close.Close()}
        />,
      );
    }
  };

  const aLabel = isPaused ? "继续" : isRunning ? "暂停" : isDone ? "" : "";
  const xLabel = isDone || isFailed ? "清除" : (isRunning || isPaused) ? "取消任务" : "";

  return (
    <Focusable
      className="nf-dlcard"
      style={{
        display: "flex", alignItems: "flex-start",
        padding: 12,
        width: "100%", boxSizing: "border-box",
      } as any}
      onOKButton={aLabel ? handleA : undefined}
      onOKActionDescription={aLabel || undefined}
      onSecondaryButton={xLabel ? handleX : undefined}
      onSecondaryActionDescription={xLabel || undefined}
    >
      {/* 封面 + 环形进度 */}
      <div style={{ flexShrink: 0, position: "relative", width: SVG_W, height: SVG_H }}>
        <div style={{
          position: "absolute", left: GAP + STROKE_W, top: GAP + STROKE_W,
          width: RECT_W, height: RECT_H, overflow: "hidden",
          background: "rgba(255,255,255,0.04)", borderRadius: 0,
        }}>
          {cover ? (
            <img src={cover} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} alt="" />
          ) : (
            <div style={{
              width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 20, color: "rgba(255,255,255,0.12)",
            }}>{name.charAt(0) || "?"}</div>
          )}
        </div>
        <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
          viewBox={`0 0 ${SVG_W} ${SVG_H}`} preserveAspectRatio="none">
          <rect x={STROKE_W / 2} y={STROKE_W / 2} width={RING_W + STROKE_W} height={RING_H + STROKE_W}
            fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={STROKE_W} />
          {isRunning && (
            <rect x={STROKE_W / 2} y={STROKE_W / 2} width={RING_W + STROKE_W} height={RING_H + STROKE_W}
              fill="none" stroke={borderColor} strokeWidth={STROKE_W}
              strokeDasharray={PERIMETER} strokeDashoffset={dashOffset}
              style={{ transition: "stroke-dashoffset 400ms ease" }} />
          )}
          {(isPaused || isDone || isFailed) && (
            <rect x={STROKE_W / 2} y={STROKE_W / 2} width={RING_W + STROKE_W} height={RING_H + STROKE_W}
              fill="none" stroke={borderColor} strokeWidth={STROKE_W} />
          )}
        </svg>
        {isPaused && (
          <div style={{
            position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
            background: "rgba(0,0,0,0.4)", borderRadius: 0,
          }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: "#f5a623" }}>⏸</span>
          </div>
        )}
      </div>

      {/* 标题 + 状态 */}
      <div style={{ flex: 1, minWidth: 0, paddingLeft: 12, paddingTop: 8 }}>
        <div style={{
          fontSize: 14, fontWeight: 600, color: "#f4f8fc",
          overflowWrap: "anywhere",
        }}>{name || "下载中..."}</div>
        <div style={{ fontSize: 12, color: "#8b929a", marginTop: 4, overflowWrap: "anywhere" }}>
          {isFailed ? (task.import_error || task.extract_error || task.error || "失败，请从游戏详情重新安装")
            : isImportDone ? (task.game_id?.endsWith("_emulator") ? "模拟器已部署" : "已导入 Steam")
            : isPaused ? "已暂停"
            : isImporting ? "导入中"
            : isDone ? "已完成"
            : isExtracting ? `解压 ${pct.toFixed(0)}%`
            : `${pct.toFixed(1)}%  ${speed}`}
        </div>
      </div>
    </Focusable>
  );
}

function formatSpeed(bps: number): string {
  if (bps < 1024) return `${bps} B/s`;
  if (bps < 1048576) return `${(bps / 1024).toFixed(1)} KB/s`;
  return `${(bps / 1048576).toFixed(1)} MB/s`;
}

const CSS = `
.nfd-downloads{display:flex;flex-direction:column;gap:12px;min-width:0}
.nfd-downloads .nf-dlcard{position:relative;min-width:0;background:#192633;border:1px solid #314353;border-radius:6px}
.nfd-downloads .nf-dlcard.gpfocus,.nfd-downloads .nf-dlcard:focus{outline:2px solid #83cfff;outline-offset:2px}
`;
