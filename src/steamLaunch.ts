import { libraryCommitImport, libraryLinkShortcut, libraryLaunchInfo } from "./api";
import type { ApiResponse, LaunchInfo } from "./api";

export function requireData<T>(result: ApiResponse<T>): T {
  if (result?.status !== "success" || !result.data) throw new Error(result?.message || "操作失败，请重试");
  return result.data;
}

export function announceLibraryChange() {
  window.dispatchEvent(new CustomEvent("newfreedeck-installed-changed"));
}

const inFlight = new Map<string, Promise<number>>();
async function createOrReuse(info: LaunchInfo): Promise<number> {
  const apps = (window as any).SteamClient?.Apps;
  if (!apps) throw new Error("Steam 客户端未就绪，请在游戏模式下重试");
  const store = (window as any).appStore;
  const existing = info.appid >>> 0;
  if (existing && (!store?.GetAppOverviewByAppID || store.GetAppOverviewByAppID(existing))) {
    if (info.token && typeof apps.SetShortcutLaunchOptions === "function") await apps.SetShortcutLaunchOptions(existing, info.launch_options);
    if (info.token) requireData(await libraryCommitImport({ token: info.token, appid: existing }));
    return existing;
  }
  if (!info.executable) throw new Error("Steam 快捷方式已丢失，请从模拟器管理重新导入 ROM，或重新安装游戏");
  if (typeof apps.AddShortcut !== "function") throw new Error("Steam 不支持创建快捷方式");
  let created = 0;
  try {
    created = Number(await apps.AddShortcut(info.title, info.executable, info.start_dir, info.launch_options)) >>> 0;
    if (!created) throw new Error("Steam 未返回有效快捷方式编号");
    if (info.compat_tool && apps.SpecifyCompatTool) await apps.SpecifyCompatTool(created, info.compat_tool);
    if (info.token) requireData(await libraryCommitImport({ token: info.token, appid: created }));
    else requireData(await libraryLinkShortcut({ game_id: info.game_id, appid: created }));
    announceLibraryChange();
    return created;
  } catch (error) {
    if (created && apps.RemoveShortcut) {
      try { await apps.RemoveShortcut(created); } catch { /* original error is more useful */ }
    }
    throw error;
  }
}

export async function ensureShortcut(info: LaunchInfo): Promise<number> {
  const current = inFlight.get(info.game_id);
  if (current) return current;
  const promise = createOrReuse(info);
  inFlight.set(info.game_id, promise);
  try { return await promise; } finally { inFlight.delete(info.game_id); }
}

export async function runShortcut(appid: number): Promise<void> {
  const apps = (window as any).SteamClient?.Apps;
  const overview = (window as any).appStore?.GetAppOverviewByAppID?.(appid >>> 0);
  // Non-Steam RunGame needs the 64-bit shortcut GameID, even before appStore refreshes.
  const gameId = overview?.m_gameid || ((BigInt(appid >>> 0) << BigInt(32)) | BigInt(0x02000000)).toString();
  if (typeof apps?.RunGame === "function") await apps.RunGame(String(gameId), "", -1, 100);
  else if (typeof apps?.RunApp === "function") await apps.RunApp(appid >>> 0, "", "");
  else throw new Error("Steam 启动接口不可用，请从 Steam 库中启动");
}

export async function launchLibraryGame(gameId: string): Promise<void> {
  const info = requireData(await libraryLaunchInfo({ game_id: gameId }));
  await runShortcut(await ensureShortcut(info));
}
