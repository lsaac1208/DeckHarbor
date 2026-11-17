import QuickAccessEntry from "./QuickAccessEntry";
import { getSettings } from "./api";
/* ═══════════════════════════════════════════
   NewFreedeck — Steam 库标签注入（游戏模式 / 库页面）

   问题：NewFreedeck 安装的游戏是非 Steam shortcut，Steam 原生
   只会把它们放进 "非 Steam" 标签页（默认 tab id 其实是
   "DesktopApps"），"已安装" 标签页永远不包含它们，因为
   AppOverview.installed 对 non-Steam 应用恒为 false。

   方案（借鉴 Unifideck v0.7.2 / TabMaster 同款技术）：
     1. routerHook.addPatch("/library") 拦截 Steam 库页面路由；
     2. afterPatch 两层 React element 到达持有 tabs 数组的
        memo 组件；
     3. 拦截其 useMemo，把默认 "Installed" / "DesktopApps"
        两个 tab 原位替换成自定义 tab（顺序、标题均不变）：
          - 已安装 = Steam 原生已安装 ∪ NewFreedeck 已安装
            （installed_games.json 里的 steam_appid）；
          - 非 Steam = 其余 non-Steam shortcuts，即排除
            NewFreedeck 自己的游戏。
     4. 自定义 tab 的 collection 数据源取自 Steam collectionStore
        的 "type-games"（TabMaster 同款路径），过滤逻辑是纯 JS。

   任何一步失败都原样放行——绝不破坏 Steam 原生库页面。
   ═══════════════════════════════════════════ */

import { routerHook } from "@decky/api";
import { afterPatch, replacePatch, wrapReactType, findInReactTree, gamepadTabbedPageClasses, type Patch } from "@decky/ui";
import React, { ReactElement } from "react";
import { installedShortcutAppIds, logFrontendDiag } from "./api";

let showLibraryEntry = true;

async function refreshLibrarySettings() {
  try {
    const r = await getSettings();
    if (r.status === "success" && r.data) {
      const next = r.data.show_freedeck_library_entry !== false;
      if (next !== showLibraryEntry) { showLibraryEntry = next; bumpInstalledVersion(); }
    }
  } catch { /* preserve last known preference */ }
}

const NON_STEAM_APP_TYPE = 1073741824; // 0x40000000

/* ── NewFreedeck 已安装 AppID 缓存 ─────────── */

// signed / unsigned 双写——Steam 不同界面可能给其中任一形式
const nfInstalledAppIds = new Set<number>();

function variantIds(appid: number): number[] {
  const ids = new Set<number>([appid]);
  if (appid < 0) ids.add(appid + 0x100000000);
  if (appid >= 0 && appid > 0x7fffffff) ids.add(appid - 0x100000000);
  return [...ids];
}

let installedVersion = 0;
const installedListeners = new Set<() => void>();

function bumpInstalledVersion(): void {
  installedVersion++;
  installedListeners.forEach((l) => {
    try { l(); } catch (e) { console.error("[NewFreedeck] installed listener failed", e); }
  });
}

export function getInstalledVersion(): number {
  return installedVersion;
}

export function onInstalledChanged(cb: () => void): () => void {
  installedListeners.add(cb);
  return () => { installedListeners.delete(cb); };
}

/** 从后端刷新已安装游戏的 shortcut AppID 集合；有变化才触发重建。 */
export async function refreshInstalledAppIds(): Promise<void> {
  try {
    const resp = await installedShortcutAppIds();
    const raw = (resp as { data?: { appids?: number[] } })?.data?.appids;
    const appids = Array.isArray(raw) ? raw : [];
    const next = new Set<number>();
    for (const n of appids) {
      const id = Number(n);
      if (!Number.isInteger(id) || id <= 0) continue;
      for (const v of variantIds(id)) next.add(v);
    }
    if (next.size === nfInstalledAppIds.size) {
      let same = true;
      for (const id of next) { if (!nfInstalledAppIds.has(id)) { same = false; break; } }
      if (same) return; // 无变化
    }
    nfInstalledAppIds.clear();
    next.forEach((id) => nfInstalledAppIds.add(id));
    console.log(`[NewFreedeck] installed shortcuts refreshed: ${next.size / 2} games (x2 variants)`);
    bumpInstalledVersion();
  } catch (e) {
    console.error("[NewFreedeck] refreshInstalledAppIds failed", e);
  }
}

/* ── 过滤器 ─────────────────────────────── */

function isNonSteamOverview(appid: number, appType: number | undefined): boolean {
  return appType === NON_STEAM_APP_TYPE || appid < 0 || appid >= 0x80000000;
}

/** 已安装 tab：Steam 原生已安装 ∪ NewFreedeck 已安装。 */
function filterInstalled(appid: number, appType: number | undefined, steamInstalled: boolean | undefined): boolean {
  if (isNonSteamOverview(appid, appType)) return nfInstalledAppIds.has(appid);
  return steamInstalled === true;
}

/** 非 Steam tab：排除 NewFreedeck 自己的游戏，其余 shortcut 保持原样。 */
function filterNonSteam(appid: number, appType: number | undefined): boolean {
  if (!isNonSteamOverview(appid, appType)) return false;
  return !nfInstalledAppIds.has(appid);
}

/* ── 自定义 tab 容器（Unifideck 同款，精简） ── */

interface SteamAppOverviewLike {
  appid: number;
  app_type?: number;
  installed?: boolean;
  display_name?: string;
}

interface SteamAppFilter {
  Matches: (a: SteamAppOverviewLike) => boolean;
}

type TabFilterFn = (app: SteamAppOverviewLike) => boolean;

interface SteamCollectionLike {
  AsDeletableCollection: () => null;
  AsDragDropCollection: () => null;
  AsEditableCollection: () => null;
  GetAppCountWithToolsFilter: (appFilter: SteamAppFilter | undefined) => number;
  bAllowsDragAndDrop: boolean;
  bIsDeletable: boolean;
  bIsDynamic: boolean;
  bIsEditable: boolean;
  displayName: string;
  id: string;
  allApps: SteamAppOverviewLike[];
  visibleApps: SteamAppOverviewLike[];
  apps: Map<number, SteamAppOverviewLike>;
}

interface SteamTab {
  title: string;
  id: string;
  content: ReactElement;
  footer: Record<string, unknown>;
  renderTabAddon?: () => ReactElement;
}

interface CollectionStoreLike {
  GetCollection: (id: string) => { allApps?: SteamAppOverviewLike[] } | null;
  appTypeCollectionMap?: Map<string, { allApps?: SteamAppOverviewLike[] }>;
}

class CustomTabContainer {
  id: string;
  title: string;
  filterFn: TabFilterFn;

  constructor(id: string, title: string, filterFn: TabFilterFn) {
    this.id = id;
    this.title = title;
    this.filterFn = filterFn;
  }

  buildCollection(): SteamCollectionLike {
    const make = (apps: SteamAppOverviewLike[]): SteamCollectionLike => ({
      AsDeletableCollection: () => null,
      AsDragDropCollection: () => null,
      AsEditableCollection: () => null,
      // Steam 在 Beta 中多次移动/重排过滤器形状——该函数抛错会
      // 经 tab 渲染器传导并炸掉整个库。绝不信任 shape，优雅降级。
      GetAppCountWithToolsFilter: (appFilter) => {
        try {
          if (typeof appFilter?.Matches === "function") {
            return apps.filter((a) => appFilter.Matches(a)).length;
          }
        } catch { /* fall through */ }
        return apps.length;
      },
      bAllowsDragAndDrop: false,
      bIsDeletable: false,
      bIsDynamic: false,
      bIsEditable: false,
      displayName: this.title,
      id: this.id,
      allApps: apps,
      visibleApps: apps,
      apps: new Map(apps.map((a) => [a.appid, a])),
    });

    try {
      const cs = (window as unknown as { collectionStore?: CollectionStoreLike }).collectionStore;
      // TabMaster 同款路径：appTypeCollectionMap 早于 GetCollection 水化，
      // 且不走 MobX 计算 getter（半水化时可能抛错）。
      const all =
        cs?.appTypeCollectionMap?.get("type-games") ??
        cs?.GetCollection("type-games");
      if (!all) {
        scheduleHydrationRetry();
        return make([]);
      }
      resetHydrationRetry();
      const filtered = (all.allApps ?? []).filter(this.filterFn);
      return make(filtered);
    } catch (e) {
      console.error("[NewFreedeck] buildCollection failed", e);
      return make([]);
    }
  }

  getActualTab(
    TabAppGrid: React.ComponentType<Record<string, unknown>>,
    TabContext: React.Context<{ label: string }> | null,
    sortingProps: Record<string, unknown>,
    collectionAppFilter: SteamAppFilter | undefined,
    templateFooter?: Record<string, unknown>,
  ): SteamTab {
    const collection = this.buildCollection();
    const inner = React.createElement(TabAppGrid, {
      collection,
      ...sortingProps,
    });
    const content = TabContext
      ? React.createElement(
          TabContext.Provider,
          { value: { label: this.title } },
          inner,
        )
      : inner;
    return {
      title: this.title,
      id: this.id,
      // 继承 AllGames 模板的 footer（按键提示、菜单回调）。Steam 的
      // 手柄 tab 渲染器需要这些字段；空对象会渲染成无操作占位条目。
      footer: { ...(templateFooter ?? {}) },
      content,
      // 每次 tab strip 渲染都会调用；计数防御性计算，任何形状异常
      // 都不允许逃逸到 Steam 的库 error boundary。
      renderTabAddon: () => {
        let count: number;
        try {
          count = collection.GetAppCountWithToolsFilter(collectionAppFilter);
        } catch {
          count = collection.visibleApps.length;
        }
        return React.createElement(
          "span",
          { className: gamepadTabbedPageClasses?.TabCount ?? "" },
          count,
        ) as ReactElement;
      },
    };
  }
}

/* ── collectionStore 水化重试 ─────────────── */

// 插件初始化时 Steam 的 type-games collection 可能尚未水化——此时
// 若把 collection 留空，所有 tab 会 latch 到 0 直到下一次无关的
// 重渲染。带退避地 ping 几次以便 store 就绪后回填（Unifideck UD-071）。
const _HYDRATION_RETRY_MAX = 8;
const _HYDRATION_RETRY_BASE_MS = 400;
let hydrationRetryCount = 0;
let hydrationRetryTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleHydrationRetry(): void {
  if (hydrationRetryTimer !== null) return;
  if (hydrationRetryCount >= _HYDRATION_RETRY_MAX) return;
  hydrationRetryCount += 1;
  hydrationRetryTimer = setTimeout(() => {
    hydrationRetryTimer = null;
    bumpInstalledVersion();
  }, _HYDRATION_RETRY_BASE_MS * hydrationRetryCount);
}

function resetHydrationRetry(): void {
  hydrationRetryCount = 0;
  if (hydrationRetryTimer !== null) {
    clearTimeout(hydrationRetryTimer);
    hydrationRetryTimer = null;
  }
}

/* ── tabs useMemo 拦截 ────────────────────── */

let cachedTabAppGrid: React.ComponentType<Record<string, unknown>> | undefined;

/* ── 诊断上报（写 plugin.log） ────────────────── */

// ××2951/cef console 在库内嵌页面不可靠；把关键阶段/失败原因经后端
// 写入 plugin.log，便于在不复现的情况下远程排查。每个 kind 只在内容
// 变化时上报一次，避免刷屏。
const lastDiagByKind = new Map<string, string>();

function diag(kind: string, info: Record<string, unknown>): void {
  try {
    const payload = JSON.stringify({ kind, ...info });
    if (lastDiagByKind.get(kind) === payload) return;
    lastDiagByKind.set(kind, payload);
    void logFrontendDiag({ message: `[LibraryTabs] ${payload}` }).catch(() => {});
  } catch {
    /* 诊断失败绝不影响主流程 */
  }
}

type ReactComponentWithType = ReactElement & {
  type: { type?: unknown; _context?: React.Context<{ label: string }> | null };
};

interface ReactHooks {
  useMemo?: <T>(fn: () => T, deps: unknown[]) => T;
  useEffect?: unknown;
}

function getReactHooks(): ReactHooks | null {
  const reactInternals = (
    window as unknown as {
      SP_REACT?: {
        __SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED?: {
          ReactCurrentDispatcher?: { current?: ReactHooks };
        };
        __CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE?: Record<
          string,
          ReactHooks | undefined
        >;
      };
    }
  ).SP_REACT;
  const current =
    reactInternals?.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED
      ?.ReactCurrentDispatcher?.current;
  if (current?.useMemo) return current;
  const clientInternals =
    reactInternals?.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  if (clientInternals) {
    for (const candidate of Object.values(clientInternals)) {
      if (candidate?.useMemo && candidate.useEffect) return candidate;
    }
  }
  return null;
}

interface TabsTupleShape {
  isTuple: boolean;
  tabs: SteamTab[];
  rest: unknown[];
}

function detectTabsShape(result: unknown[]): TabsTupleShape | null {
  if (
    result.length >= 2 &&
    Array.isArray(result[0]) &&
    (result[0] as SteamTab[])[0]?.id &&
    (result[0] as SteamTab[])[0]?.content
  ) {
    return {
      isTuple: true,
      tabs: result[0] as SteamTab[],
      rest: result.slice(1),
    };
  }
  const first = result[0] as SteamTab | undefined;
  if (first?.id && first.content) {
    return { isTuple: false, tabs: result as SteamTab[], rest: [] };
  }
  return null;
}

const loggedBailReasons = new Set<string>();
function logBailOnce(reason: string, extra?: unknown): void {
  if (loggedBailReasons.has(reason)) return;
  loggedBailReasons.add(reason);
  console.warn(`[NewFreedeck Library] spliceTabs bailed: ${reason}`, extra ?? "");
  // 同步写入 plugin.log —— cef console 在此上下文可能丢失
  let extraStr = "";
  try { extraStr = JSON.stringify(extra ?? ""); } catch { extraStr = String(extra ?? ""); }
  diag("splice-bail", { reason, extra: extraStr.slice(0, 400) });
}

function isAppFilter(v: unknown): v is SteamAppFilter {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as SteamAppFilter).Matches === "function"
  );
}

/** 按形状（而非位置）在 useMemo deps 中定位 Steam 的 collections
 *  app filter——Steam 客户端版本会重排 deps 数组（2026-07 beta
 *  就插入过一个 dep）。带 Matches 的 dep 是唯一判据。 */
function resolveAppFilter(deps: unknown[]): SteamAppFilter | undefined {
  const found = deps.find(isAppFilter);
  if (!found && !loggedBailReasons.has("app-filter-missing")) {
    loggedBailReasons.add("app-filter-missing");
    console.warn(
      "[NewFreedeck Library] collections app filter not found in useMemo deps; tab counts fall back to unfiltered",
      deps.map((d) => typeof d),
    );
  }
  return found;
}

function spliceTabs(result: unknown, deps: unknown[]): unknown {
  if (!Array.isArray(result)) {
    logBailOnce("result is not an array", result);
    return result;
  }
  const shape = detectTabsShape(result);
  if (!shape) {
    logBailOnce("detectTabsShape returned null", result);
    return result;
  }
  // 幂等：同一路由重渲染不应重复 splice
  if (shape.tabs.some((t) => t.id?.startsWith("newfreedeck-"))) return result;

  const [eSortBy, setSortBy, showSortingContextMenu] = deps as [unknown, unknown, unknown];
  const sortingProps = { eSortBy, setSortBy, showSortingContextMenu };
  const collectionsAppFilter = resolveAppFilter(deps);

  // 模板永远从 AllGames 取（内容最全、无条件存在）
  const template = shape.tabs.find((t) => t.id === "AllGames");
  if (!template) {
    logBailOnce("no AllGames template tab in", shape.tabs.map((t) => t.id));
    return result;
  }
  const TabAppGrid =
    cachedTabAppGrid ??
    (
      findInReactTree(template.content, (elt: unknown) =>
        Boolean((elt as { type?: { toString?: () => string } })?.type?.toString?.().includes("Library_FilteredByHeader")),
      ) as { type?: React.ComponentType<Record<string, unknown>> } | null
    )?.type;
  if (!TabAppGrid) {
    logBailOnce("Library_FilteredByHeader component not found in AllGames template");
    return result;
  }
  cachedTabAppGrid = TabAppGrid;

  const TabContext =
    (template.content as ReactComponentWithType).type?._context ?? null;
  const templateFooter = (template.footer ?? {}) as Record<string, unknown>;

  let replacedCount = 0;
  const replaced = shape.tabs.map((t): SteamTab => {
    if (t.id !== "Installed" && t.id !== "DesktopApps") return t;
    const filterFn: TabFilterFn =
      t.id === "Installed"
        ? (app) => filterInstalled(app.appid, app.app_type, app.installed)
        : (app) => filterNonSteam(app.appid, app.app_type);
    const container = new CustomTabContainer(
      t.id === "Installed" ? "newfreedeck-installed" : "newfreedeck-nonsteam",
      t.title, // 沿用原生 tab 的本地化标题
      filterFn,
    );
    const custom = container.getActualTab(
      TabAppGrid,
      TabContext,
      sortingProps,
      collectionsAppFilter,
      templateFooter,
    );
    replacedCount++;
    return custom;
  });

  if (showLibraryEntry) {
    replaced.push({ id: "newfreedeck-entry", title: "DeckHarbor", footer: { ...templateFooter }, content: React.createElement(QuickAccessEntry) });
  }
  if (replacedCount === 0 && !showLibraryEntry) {
    logBailOnce("neither Installed nor DesktopApps tab found", shape.tabs.map((t) => t.id));
    return result;
  }
  console.log(
    `[NewFreedeck Library] replaced ${replacedCount} default tab(s) (Installed/DesktopApps)`,
  );
  diag("splice-ok", {
    replaced: replacedCount,
    tabIds: shape.tabs.map((t) => t.id),
    installedCount: nfInstalledAppIds.size / 2,
  });

  if (shape.isTuple) return [replaced, ...shape.rest];
  return replaced;
}

/* ── /library 路由补丁 ────────────────────── */

type LibraryRouteProps = { path: string; children: ReactElement };

/** 补丁：拦截持有 tabs 数组的 memo 组件的 useMemo（Unifideck 同款）。
 *
 *  注意：本函数绝不允许在 patch 回调里调用 useState/useEffect。
 *  patch 由 decky loader 的 gamepadRouterWrapper 渲染期调用，此时
 *  React dispatcher 可能属于另一个 React 实例（库内嵌页与外壳是
 *  两套 webpack universe），直接调用 hooks 会抛
 *  "Minified React error #310 (Invalid hook call)"，导致整个注入被
 *  中止（旧实现就是这么废掉的）。
 *
 *  新鲜度不依赖订阅式重渲染：拦截器把 installedVersion 追加进 tabs
 *  useMemo 的真实 deps。安装/卸载后 Steam 自身会因 AppState 变化
 *  重渲染库（游戏会立刻出现在"非 Steam"网格里就是证据），该次渲染
 *  中 deps 变化 → useMemo 重算 → spliceTabs 用最新的 AppID 集合
 *  重跑，游戏随即挪进"已安装"。 */
function applyLibraryPatch(): () => void {
  let disposed = false;

  const patch = (rawProps: unknown): unknown => {
    if (disposed) return rawProps;
    const props = rawProps as LibraryRouteProps;
    if (!props?.children) return rawProps;

    try {
      afterPatch(
        props.children as never,
        "type",
        (_: unknown[], ret1: ReactElement) => {
          if (!ret1?.type) return ret1;
          let innerPatch: Patch | undefined;
          let memoCache: unknown;

          afterPatch(
            ret1 as never,
            "type",
            (_2: unknown[], ret2: ReactElement) => {
              if (!ret2?.type) return ret2;
              const ret2t = ret2 as ReactComponentWithType;
              if (memoCache) {
                ret2t.type = memoCache as ReactComponentWithType["type"];
                return ret2;
              }
              const origMemoComponent = ret2t.type.type as (...args: unknown[]) => unknown;
              // 新 Steam 构建某些渲染路径上 .type.type 可能为 null——
              // 包装 undefined 会炸掉整个库 tab。原样放行。
              if (typeof origMemoComponent !== "function") {
                diag("patch-bail-no-memo", {});
                return ret2;
              }
              wrapReactType(ret2 as never);
              innerPatch = replacePatch(
                ret2t.type as never,
                "type",
                (args: unknown[]) => {
                  const hooks = getReactHooks();
                  if (!hooks?.useMemo) {
                    // 没有活动 dispatcher = 当前渲染不属于 SP_REACT
                    // 对应的 React 实例。原样渲染，并上报以便诊断。
                    diag("intercept-no-dispatcher", {});
                    return origMemoComponent(...args);
                  }
                  const realUseMemo = hooks.useMemo;
                  hooks.useMemo = <T,>(fn: () => T, useMemoDeps: unknown[]): T => {
                    const enrichedDeps = [...useMemoDeps, getInstalledVersion()];
                    return realUseMemo(
                      () => spliceTabs(fn(), enrichedDeps) as T,
                      enrichedDeps,
                    );
                  };
                  try {
                    return origMemoComponent(...args);
                  } finally {
                    hooks.useMemo = realUseMemo;
                  }
                },
              );
              memoCache = ret2t.type;
              diag("patch-installed", { level2: true });
              return ret2;
            },
          );
          return ret1;
        },
      );
      diag("patch-wired", { hasChildren: true });
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      console.error("[NewFreedeck] library patch failed", e);
      diag("patch-error", { message });
    }
    return rawProps;
  };

  routerHook.addPatch("/library", patch as never);
  return () => {
    disposed = true;
    try {
      routerHook.removePatch("/library", patch as never);
    } catch (e) {
      console.warn("[NewFreedeck] removePatch failed", e);
    }
  };
}

/* ── 启动 / 停止 ──────────────────────────── */

let pollTimer: ReturnType<typeof setInterval> | null = null;
let disposePatch: (() => void) | null = null;

function onInstallStateEvent(): void {
  void refreshInstalledAppIds();
}

/** 插件入口调用：挂载库 tab 补丁 + 启动已安装状态轮询。 */
export function startLibraryTabManager(): void {
  if (disposePatch) return;
  disposePatch = applyLibraryPatch();
  void refreshInstalledAppIds();
  void refreshLibrarySettings();
  window.addEventListener("newfreedeck-settings-changed", refreshLibrarySettings);
  // 后端文件读取很轻量——轮询兜底（覆盖商店页/其他入口的安装）
  pollTimer = setInterval(() => void refreshInstalledAppIds(), 10000);
  // 安装 overlay 完成导入后立即刷新
  window.addEventListener("newfreedeck-installed-changed", onInstallStateEvent);
  console.log("[NewFreedeck] library tab manager started");
}

export function stopLibraryTabManager(): void {
  disposePatch?.();
  disposePatch = null;
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  window.removeEventListener("newfreedeck-installed-changed", onInstallStateEvent);
  window.removeEventListener("newfreedeck-settings-changed", refreshLibrarySettings);
}