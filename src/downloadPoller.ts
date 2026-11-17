import { toaster } from "@decky/api";
import { downloadFailed, downloadSucceeded } from "./downloadState";
/* ═══════════════════════════════════════════
   NewFreedeck — 全局下载轮询器（含 Steam 导入）
   ═══════════════════════════════════════════ */

import { downloadPoll, downloadCancel, downloadStart, downloadList, downloadPause, downloadResume, getPendingArtwork, importApplied, getSettings, deleteArchive, steamFinishImport } from "./api";
import { upsertDownload, removeDownload, getDownloadQueue } from "./downloadStore";
import { applyRomLaunchOptions } from "./romImport";

let _poller: DownloadPoller | null = null;
export function getDownloadPoller(): DownloadPoller {
  if (!_poller) _poller = new DownloadPoller();
  return _poller;
}

class DownloadPoller {
  private _intervals = new Map<string, ReturnType<typeof setInterval>>();
  private _polling = new Set<string>();
  private _importing = new Map<string, boolean>(); // 防重复导入
  private _doneNotified = new Set<string>();       // 已完成已弹 toast

  /** 开始轮询一个下载任务 */
  addTask(taskId: string, coverUrl?: string) {
    if (this._intervals.has(taskId)) return;
    const id = setInterval(async () => {
      if (this._polling.has(taskId)) return;
      this._polling.add(taskId);
      try {
        const poll = await downloadPoll({ task_id: taskId });
        if (this._intervals.get(taskId) !== id) return;
        if (poll?.status === "success" && poll.data) {
          const data = poll.data as any;
          upsertDownload(data, coverUrl);

          // 前端创建快捷方式（代替 steamos-add-to-steam）
          if (data.import_status === "need_shortcut" && data.steam_exe && !this._importing.get(taskId)) {
            this._importing.set(taskId, true);
            this._createShortcutAndFinish(data);
          }

          // 触发 Steam 导入
          if (data.import_status === "applying" && data.steam_appid && !this._importing.get(taskId)) {
            this._importing.set(taskId, true);
            this._runSteamImport(data);
          }

          // 完成后处理
          if (this._isAllDone(data)) {
            this._stopPolling(taskId);
            this._handleTaskDone(taskId, data);
          }
        }
      } catch { /* 下次轮询重试 */ }
      finally { this._polling.delete(taskId); }
    }, 1000);
    this._intervals.set(taskId, id);
  }

  private async _controlTask(taskId: string, action: "pause" | "resume" | "cancel") {
    try {
      const r = await (action === "pause" ? downloadPause : action === "resume" ? downloadResume : downloadCancel)({ task_id: taskId });
      if (r?.status !== "success") throw new Error(r?.message || "下载任务操作失败");
      if (action === "cancel") {
        this._stopPolling(taskId); removeDownload(taskId);
        this._doneNotified.delete(taskId); this._importing.delete(taskId);
      } else {
        if (r.data) upsertDownload(r.data as any);
        if (action === "pause") this._stopPolling(taskId);
        else this.addTask(taskId);
      }
    } catch (e: any) {
      toaster.toast({ title: "下载任务", body: e?.message || "操作失败，请重试" });
    }
  }

  async cancelTask(taskId: string) { await this._controlTask(taskId, "cancel"); }
  async pauseTask(taskId: string) { await this._controlTask(taskId, "pause"); }
  async resumeTask(taskId: string) { await this._controlTask(taskId, "resume"); }

  /** 页面挂载时恢复轮询 + 从后端同步 */
  async resumeAll() {
    try {
      const list = await downloadList();
      if (list?.status === "success" && list.data?.tasks?.length) {
        for (const t of list.data.tasks) {
          if (downloadSucceeded(t)) { removeDownload(t.task_id); continue; }
          upsertDownload(t as any);
        }
      }
    } catch {}

    const queue = getDownloadQueue();
    for (const task of queue) {
      if (task.status !== "paused" && !this._isAllDone(task) && !this._intervals.has(task.task_id)) {
        this.addTask(task.task_id, (task as any).cover_url);
      }
    }
  }

  destroy() {
    for (const id of this._intervals.keys()) clearInterval(this._intervals.get(id));
    this._intervals.clear();
    this._importing.clear();
    this._doneNotified.clear();
  }

  /* ═══ 内部 ═══ */

  private _stopPolling(taskId: string) {
    const id = this._intervals.get(taskId);
    if (id) { clearInterval(id); this._intervals.delete(taskId); }
  }

  private _isAllDone(t: any): boolean {
    return downloadFailed(t) || downloadSucceeded(t);
  }

  private async _handleTaskDone(taskId: string, data: any) {
    if (downloadFailed(data)) return; // 保留失败任务和下载文件供排查
    if (this._doneNotified.has(taskId)) return; // 已通知过
    this._doneNotified.add(taskId);

    // 检查是否开启安装后自动删除压缩包
    try {
      const s = await getSettings();
      if (s?.data?.auto_delete_after_install && data?.file_path) {
        await deleteArchive({ file_path: data.file_path });
      }
    } catch {}

    // 1 秒后从队列移除
    setTimeout(() => {
      removeDownload(taskId);
      this._doneNotified.delete(taskId);
      this._importing.delete(taskId);
    }, 1000);
  }

  /* ─── Steam 导入（在 poller 中运行，不依赖任何页面） ─── */

  /** 前端创建 Steam 快捷方式，拿到 appid 后回传后端继续配置 */
  private async _createShortcutAndFinish(task: any) {
    const taskId = task.task_id;
    const exe = String(task.steam_exe || "");
    const gameTitle = String(task.game_title || "");
    const startDir = String(task.start_dir || "");
    const launchOpts = String(task.launch_options || "");

    try {
      const SC = (window as any).SteamClient;
      if (!SC?.Apps) {
        throw new Error("Steam 客户端暂不可用，请稍后重新安装游戏");
      }

      // 创建快捷方式（照搬"打开"按钮的做法）
      const steamAppId = await SC.Apps.AddShortcut(gameTitle, exe, startDir, launchOpts);
      console.log(`[NewFreedeck] _createShortcutAndFinish: AddShortcut appId=${steamAppId} exe=${exe}`);

      // 等待 Steam 识别
      const appStore = (window as any).appStore;
      let gameInfo: any = null;
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 100));
        gameInfo = appStore?.GetAppOverviewByAppID(steamAppId);
        if (gameInfo != null) break;
      }

      if (!gameInfo) {
        console.log(`[NewFreedeck] _createShortcutAndFinish: app not found, cleaning up`);
        try { await SC.Apps.RemoveShortcut(steamAppId); } catch {}
        throw new Error("Steam 未能识别新建的快捷方式");
      }

      // 设置名称（确保中文标题生效）
      if (gameTitle && SC.Apps.SetShortcutName) {
        try { await SC.Apps.SetShortcutName(steamAppId, gameTitle); } catch {}
      }

      // ROM 游戏：设置启动选项
      if (launchOpts && SC.Apps.SetShortcutLaunchOptions) {
        try { await SC.Apps.SetShortcutLaunchOptions(steamAppId, launchOpts); } catch {}
      }

      // 回传 appid 给后端继续配置（兼容层 + VDF 修改 + 封面）
      const r = await steamFinishImport({ task_id: taskId, appid: steamAppId });
      console.log(`[NewFreedeck] _createShortcutAndFinish: steamFinishImport status=${r?.status}`);

      // 后端已将 import_status 设为 "applying"，立即触发后续导入流程
      if (r?.status !== "success") throw new Error(r?.message || "Steam 导入配置失败");
      if (r?.status === "success") {
        const poll = await downloadPoll({ task_id: taskId });
        if (poll?.status === "success" && poll.data) {
          const data = poll.data as any;
          upsertDownload(data);
          if (data.import_status === "applying" && data.steam_appid) {
            await this._runSteamImport(data);
          }
        }
      }
    } catch (e: any) {
      console.log(`[NewFreedeck] _createShortcutAndFinish error: ${e?.message}`);
      await importApplied({ task_id: taskId, error: e?.message || "Steam 导入失败" });
    } finally {
      this._importing.delete(taskId);
    }
  }

  private async _runSteamImport(task: any) {
    const taskId = task.task_id;
    const appid = task.steam_appid;
    const gameTitle = task.game_title || "";
    const compatTool = task.compat_tool || "";

    try {
      const SC = (window as any).SteamClient;
      if (!SC?.Apps) {
        throw new Error("Steam 客户端暂不可用");
      }

      // 兼容层
      if (compatTool && SC.Apps.SpecifyCompatTool) {
        try { SC.Apps.SpecifyCompatTool(appid, compatTool); } catch {}
      }

      // 注册 app details
      try {
        await new Promise<void>((resolve) => {
          const t = setTimeout(resolve, 3000);
          try {
            const { unregister } = SC.Apps.RegisterForAppDetails(appid, () => {
              clearTimeout(t); unregister(); resolve();
            });
          } catch { clearTimeout(t); resolve(); }
        });
      } catch {}

      // 设置名称
      if (gameTitle && SC.Apps.SetShortcutName) {
        try { await SC.Apps.SetShortcutName(appid, gameTitle); } catch {}
      }

      // ROM 导入：实时设置启动选项
      const launchOpts = task.launch_options || "";
      if (launchOpts) {
        try {
          await applyRomLaunchOptions(appid, gameTitle, launchOpts);
        } catch {}
      }

      // 等封面（最多 30s）
      const artwork = await this._waitForArtwork(appid);
      if (!artwork) {
        await importApplied({ task_id: taskId });
        return;
      }

      // hero 缺失但有 grid 时本地合成 hero 横幅（库主页大图不再空白）
      let hero = (artwork as any)["hero"] as { data: string; format: string } | undefined;
      if (!hero?.data && (artwork as any)["grid"]?.data) {
        hero = await this._makeHeroFromGrid((artwork as any)["grid"]) || undefined;
        if (hero) console.log(`[NewFreedeck] hero synthesized from grid for appid=${appid}`);
      }

      // 上封面
      if (SC.Apps.SetCustomArtworkForApp) {
        for (const [key, assetType] of [["grid", 0], ["hero", 3], ["logo", 4]] as [string, number][]) {
          const asset = key === "hero" ? hero : (artwork as any)[key];
          if (!asset?.data) continue;
          try {
            const prepared = await this._ensurePng(asset);
            await SC.Apps.ClearCustomArtworkForApp(appid, assetType);
            await new Promise(r => setTimeout(r, 500));
            await SC.Apps.SetCustomArtworkForApp(appid, prepared.data, prepared.format, assetType);
          } catch {}
        }

        // logo 位置
        if ((artwork as any)["logo"]?.data) {
          try {
            const store = (window as any).appDetailsStore;
            const pos = await store?.GetCustomLogoPosition?.(appid);
            if (!pos) {
              await store?.SaveCustomLogoPosition?.(
                { appid, BIsShortcut: () => true },
                { pinnedPosition: "BottomLeft", nWidthPct: 50, nHeightPct: 50 }
              );
            }
          } catch {}
        }
      }

      await importApplied({ task_id: taskId });
      // 通知库标签管理器刷新 "已安装" tab（新安装的游戏立即可见）
      window.dispatchEvent(new CustomEvent("newfreedeck-installed-changed"));
    } catch (e: any) {
      await importApplied({ task_id: taskId, error: e?.message || "Steam 导入失败" });
    } finally {
      this._importing.delete(taskId);
    }
  }

  private async _waitForArtwork(appid: number): Promise<any> {
    for (let i = 0; i < 30; i++) {
      try {
        const r = await getPendingArtwork({ appid });
        if (r?.data && Object.keys(r.data).length > 0) return r.data;
      } catch {}
      await new Promise(r => setTimeout(r, 1000));
    }
    return null;
  }

  private async _ensurePng(asset: { data: string; format: string }) {
    if (asset.format === "png") return asset;
    try {
      const img = await this._loadImage(asset);
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || img.width;
      canvas.height = img.naturalHeight || img.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return asset;
      ctx.drawImage(img, 0, 0);
      const data = canvas.toDataURL("image/png").split(",", 2)[1];
      return data ? { data, format: "png" } : asset;
    } catch { return asset; }
  }

  private async _loadImage(asset: { data: string; format: string }): Promise<HTMLImageElement> {
    const mime = asset.format === "jpg" ? "jpeg" : asset.format;
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = reject;
      el.src = `data:image/${mime};base64,${asset.data}`;
    });
  }

  /** 用竖版封面合成 1920x620 hero 横幅：模糊填充铺底 + 居中封面。
   *  SGDB/Steam CDN 都找不到 hero 时兜底，避免库主页大图空白。 */
  private async _makeHeroFromGrid(grid: { data: string; format: string }) {
    try {
      const img = await this._loadImage(grid);
      const W = 1920, H = 620;
      const canvas = document.createElement("canvas");
      canvas.width = W;
      canvas.height = H;
      const ctx = canvas.getContext("2d");
      if (!ctx) return null;

      // 深色底
      ctx.fillStyle = "#0e141b";
      ctx.fillRect(0, 0, W, H);
      // 模糊拉伸封面做背景（cover 裁切）
      const scale = Math.max(W / img.width, H / img.height);
      const dw = img.width * scale, dh = img.height * scale;
      try { ctx.filter = "blur(36px)"; } catch { /* CEF 老内核不支持 filter 也不致命 */ }
      ctx.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
      try { ctx.filter = "none"; } catch {}
      // 压暗一层，突出中央封面
      ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
      ctx.fillRect(0, 0, W, H);
      // 居中封面（按比例缩放，高度占 88%）
      const gh = H * 0.88;
      const gw = gh * ((img.naturalWidth || img.width) / (img.naturalHeight || img.height));
      ctx.drawImage(img, (W - gw) / 2, (H - gh) / 2, gw, gh);

      const data = canvas.toDataURL("image/png").split(",", 2)[1];
      return data ? { data, format: "png" as const } : null;
    } catch { return null; }
  }

}
