import CatalogSourcesPanel from "./CatalogSourcesPanel";
import { useCatalogRevision } from "./catalogUpdates";
import { useCardColumns, PageShell, PageTabs, ActionButton as ButtonItem, SelectField as DropdownItem, Section as PanelSection } from "./ui";
import LibraryDashboard from "./LibraryDashboard";
import { EMU_MANAGER_ROUTE } from "./routes";
import { Navigation } from "@decky/ui";
/* ═══════════════════════════════════════════
   NewFreedeck — 游戏库 (Tab 缓存 + 分页加载)
   ═══════════════════════════════════════════ */

import { SteamSpinner, Focusable, TextField } from "@decky/ui";
import { toaster } from "@decky/api";
import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { getShelfGames, getPlatformGames, getCuratedShelf, listCatalog, playLaterList, playLaterRemove } from "./api";
import { ShelfView } from "./library/ShelfView";
import { GameCard, getLibraryFocusClassName } from "./library/GameCard";
import { InstallOverlay } from "./library/InstallOverlay";
import { EmulatorTab } from "./EmulatorTab";
import type { EmulatorMode } from "./EmulatorTab";
import { EmuPlatformGrid, EMU_PLATFORM_CSS } from "./EmuPlatformTab";
import { CarouselBanner } from "./library/CarouselBanner";
import type { CarouselBannerItem } from "./library/CarouselBanner";
import { focusNavigableElement, focusNavigableElementWithRetry } from "./library/focus";
import { LIBRARY_CSS, FOCUS_TRIGGER_CSS } from "./styles";
import type { CatalogItem, DownloadTask, PlayLaterItem } from "./api";
import { upsertDownload } from "./downloadStore";
import { getDownloadPoller } from "./downloadPoller";


const TAB_ORDER = ["installed", "home", "pc", "emulator", "search", "pool", "later", "sources"];
const TAB_META: Record<string, string> = {
  installed: "我的游戏", home: "发现", pc: "PC游戏", emulator: "模拟器游戏", search: "搜索", pool: "资源池", later: "稍后再玩", sources: "来源",
};

const EMU_PLATFORM_TABS: { id: EmulatorMode; title: string }[] = [
  { id: "ps1", title: "PS1" },
  { id: "ps2", title: "PS2" },
  { id: "ps3", title: "PS3" },
  { id: "psp", title: "PSP" },
  { id: "switch", title: "Switch" },
  { id: "3ds", title: "3DS" },
  { id: "gba", title: "GBA" },
  { id: "nds", title: "NDS" },
  { id: "fc", title: "FC" },
  { id: "md", title: "MD" },
];

/* ═══════════════════════════════════════════
   LibraryPage
   ═══════════════════════════════════════════ */

export default function LibraryPage({ initialPlatform }: { initialPlatform?: EmulatorMode } = {}) {
  const catalogRevision = useCatalogRevision();
  const [sourceFilter, setSourceFilter] = useState("all");
  const [refreshKey, setRefreshKey] = useState(0);
  const [tab, setTab] = useState(initialPlatform ? "emulator" : "home");
  const [mode, setMode] = useState<"normal" | "emulator">(initialPlatform ? "emulator" : "normal");
  const [activeEmuPlatform, setActiveEmuPlatform] = useState<EmulatorMode>(initialPlatform || "ps1");
  const [games, setGames] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState("");
  const loadGeneration = useRef(0);
  const moreBusy = useRef(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const cacheRef = useRef<Map<string, { games: CatalogItem[]; page: number; hasMore: boolean }>>(new Map());

  const [selectedGame, setSelectedGame] = useState<CatalogItem | null>(null);
  const [sourceRect, setSourceRect] = useState<DOMRect | null>(null);
  const lastFocusedCardRef = useRef<HTMLElement | null>(null);

  const handleOpen = useCallback((item: CatalogItem, el: HTMLElement) => {
    // 保存当前聚焦的卡片，关闭安装页后恢复
    lastFocusedCardRef.current = el.closest(".freedeck-library-grid-card") as HTMLElement | null;
    setSourceRect(el.getBoundingClientRect()); setSelectedGame(item);
  }, []);
  const handleClose = useCallback(() => {
    setSelectedGame(null); setSourceRect(null);
    // 恢复焦点到之前选中的卡片
    const restoreEl = lastFocusedCardRef.current;
    if (restoreEl) {
      lastFocusedCardRef.current = null;
      setTimeout(() => focusNavigableElement(restoreEl), 80);
    }
  }, []);

  const handleEnterEmulatorMode = useCallback((platform: EmulatorMode) => {
    setMode("emulator");
    setActiveEmuPlatform(platform);
  }, []);

  const handleStartDownload = useCallback(async (task: DownloadTask, coverUrl: string) => {
    setSelectedGame(null); setSourceRect(null);
    const initial = { ...task, cover_url: coverUrl } as any;
    upsertDownload(initial, coverUrl);
    getDownloadPoller().addTask(task.task_id, coverUrl);
  }, []);

  const handleCancelDownload = useCallback(async (taskId: string) => {
    await getDownloadPoller().cancelTask(taskId);
  }, []);

  // 页面挂载时恢复进行中的下载轮询
  useEffect(() => {
    getDownloadPoller().resumeAll();
  }, []);

  // 模拟器模式返回普通模式的处理函数
  const handleEmulatorBack = useCallback(() => {
    setMode("normal");
    setTab("emulator");
  }, []);

  const PAGE_SIZE = 25;

  const loadFirst = useCallback(async (key: "home" | "pc") => {
    const generation = ++loadGeneration.current;
    moreBusy.current = false;
    setLoadingMore(false);
    setLoadError("");
    const cached = cacheRef.current.get(key);
    if (cached) {
      setGames(cached.games); setPage(cached.page); setHasMore(cached.hasMore); setLoading(false);
      return;
    }
    setPage(1); setHasMore(false); setLoading(true);
    try {
      const r = key === "home"
        ? await getShelfGames({ limit: 24 })
        : await getPlatformGames({ platform: "pc", page: 1, page_size: PAGE_SIZE });
      if (generation !== loadGeneration.current) return;
      if (r?.status !== "success" || !r.data) throw new Error(r?.message || "游戏目录加载失败");
      const entry = { games: r.data.games, page: 1, hasMore: "has_more" in r.data && Boolean(r.data.has_more) };
      cacheRef.current.set(key, entry);
      setGames(entry.games); setHasMore(entry.hasMore);
    } catch (e: any) {
      if (generation === loadGeneration.current) setLoadError(e?.message || "游戏目录加载失败");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, [refreshKey]);

  const loadPcMore = useCallback(async () => {
    if (moreBusy.current || !hasMore) return;
    const generation = loadGeneration.current;
    moreBusy.current = true; setLoadingMore(true); setLoadError("");
    const next = page + 1;
    try {
      const r = await getPlatformGames({ platform: "pc", page: next, page_size: PAGE_SIZE });
      if (generation !== loadGeneration.current) return;
      if (r?.status !== "success" || !r.data) throw new Error(r?.message || "加载更多失败");
      const merged = [...games, ...r.data.games];
      cacheRef.current.set("pc", { games: merged, page: next, hasMore: r.data.has_more });
      setGames(merged); setHasMore(r.data.has_more); setPage(next);
    } catch (e: any) {
      if (generation === loadGeneration.current) setLoadError(e?.message || "加载更多失败");
    } finally {
      if (generation === loadGeneration.current) { moreBusy.current = false; setLoadingMore(false); }
    }
  }, [page, hasMore, games]);

  useEffect(() => {
    if (tab === "home" || tab === "pc") void loadFirst(tab);
    else { setLoading(false); setLoadError(""); }
    return () => { ++loadGeneration.current; };
  }, [tab, loadFirst]);

  const spinner = <div style={{ display: "flex", justifyContent: "center", padding: 60 }}><SteamSpinner style={{ width: 40, height: 40 }} /></div>;
  const empty = <div style={{ padding: 40, textAlign: "center" }}>暂无游戏，请稍后刷新目录</div>;
  const showSpinner = loading && games.length === 0;

  // Tab 切换后聚焦第一个内容元素
  const tabFocusIndexRef = useRef<number>(0);
  const handleShowTab = useCallback((id: string) => {
    if (tab !== id) { setGames([]); tabFocusIndexRef.current = 0; } // 切 tab 时重置
    setTab(id);
  }, [tab]);

  useEffect(() => {
    cacheRef.current.clear();
    if (!games.length) setRefreshKey(value => value + 1);
  }, [catalogRevision]);

  const normalTabs = TAB_ORDER.map(id => ({
    id, title: TAB_META[id],
    content: (id === "home" || id === "pc") && showSpinner ? spinner
      : id === "installed" ? <LibraryDashboard />
      : id === "home" ? <HomeTab games={games} onOpen={handleOpen} initialFocus={tabFocusIndexRef.current === 0} onInitialFocused={() => { tabFocusIndexRef.current = 1; }} />
      : id === "pc"   ? <GridTab games={games} hasMore={hasMore} loadingMore={loadingMore} onLoadMore={loadPcMore} onOpen={handleOpen} autoFocusFirst={tabFocusIndexRef.current === 0} onAutoFocused={() => { tabFocusIndexRef.current = 1; }} />
      : id === "emulator" ? <EmulatorTab onOpen={handleOpen} onEnterEmulatorMode={handleEnterEmulatorMode} autoFocusFirst={tabFocusIndexRef.current === 0} onAutoFocused={() => { tabFocusIndexRef.current = 1; }} />
      : id === "search" ? <SearchPool key="search" onOpen={handleOpen} />
      : id === "sources" ? <CatalogSourcesPanel onBrowse={source => { setSourceFilter(source); setTab("pool"); }} />
      : id === "later" ? <PlayLaterTab onOpen={handleOpen} />
      : id === "pool" ? <SearchPool key={`pool-${sourceFilter}`} initialSource={sourceFilter} browse onOpen={handleOpen} /> : empty,
  }));

  const emuTabs = EMU_PLATFORM_TABS.map(p => ({
    id: p.id,
    title: p.title,
    content: <EmuPlatformGrid platform={p.id} onOpen={handleOpen} />,
  }));

  const tabs = mode === "normal" ? normalTabs : emuTabs;

  return (
    <PageShell className="nf-library-root">
      <style>{LIBRARY_CSS}{FOCUS_TRIGGER_CSS}{EMU_PLATFORM_CSS}{NF_CSS}</style>

      {loadError && (tab === "home" || tab === "pc") && (
        <PanelSection><div role="alert">{loadError}</div><ButtonItem onClick={() => {
          cacheRef.current.delete(tab); void loadFirst(tab as "home" | "pc");
        }}>重新加载</ButtonItem></PanelSection>
      )}
      {mode === "emulator" && <div style={{ display: "flex", gap: 12, padding: "0 16px" }}>
        <ButtonItem onClick={handleEmulatorBack}>返回全部平台</ButtonItem>
        <ButtonItem onClick={() => Navigation.Navigate(EMU_MANAGER_ROUTE)}>管理模拟器 / 导入 ROM</ButtonItem>
      </div>}
      <div className="nfd-catalog-toolbar"><span className="nfd-muted">本地目录优先 · 新内容在后台更新</span>
        <ButtonItem onClick={() => { cacheRef.current.clear(); setRefreshKey(value => value + 1); window.dispatchEvent(new Event("newfreedeck-catalog-changed")); }}>刷新列表</ButtonItem>
      </div>
      <PageTabs
        tabs={tabs}
        activeTab={mode === "normal" ? tab : activeEmuPlatform}
        onShowTab={(id: string) => {
          if (mode === "normal") handleShowTab(id);
          else setActiveEmuPlatform(id as EmulatorMode);
        }}
      />

      {loading && games.length > 0 && (
        <div style={{ position:"fixed",bottom:0,left:0,right:0,height:2,zIndex:100,
          background:"linear-gradient(90deg,transparent,#369ff4,transparent)",
          animation:"nf-loading-bar 1.2s ease-in-out infinite" }} />
      )}

      {selectedGame && sourceRect && (
        <InstallOverlay game={selectedGame} sourceRect={sourceRect} onClose={handleClose}
          onStartDownload={handleStartDownload} />
      )}

      {/* 书架区 */}
    </PageShell>
  );
}

/* ═══════════════════════════════════════════
   HomeTab
   ═══════════════════════════════════════════ */

// 模拟器平台列表（用于首页书架）
const EMU_SHELVES = [
  { id: "switch", title: "Switch 精选与自制游戏" },
  { id: "gba", title: "GBA 精选" },
  { id: "ps1", title: "PS1 精选" },
  { id: "ps2", title: "PS2 精选" },
  { id: "psp", title: "PSP 精选" },
  { id: "nds", title: "NDS 精选" },
  { id: "3ds", title: "3DS 精选" },
  { id: "fc",  title: "FC 精选" },
  { id: "md",  title: "MD 精选" },
];

type ShelfRow = { title: string; items: CatalogItem[]; cardRefs: Map<number, HTMLDivElement> };

/* ── 资源池搜索组件 ──────────────────── */

const PLATFORM_OPTIONS = [
  { data: "all", label: "全部" },
  { data: "pc", label: "PC" },
  { data: "ps1", label: "PS1" },
  { data: "ps2", label: "PS2" },
  { data: "ps3", label: "PS3" },
  { data: "psp", label: "PSP" },
  { data: "switch", label: "Switch" },
  { data: "3ds", label: "3DS" },
  { data: "gba", label: "GBA" },
  { data: "nds", label: "NDS" },
  { data: "fc", label: "FC" },
  { data: "md", label: "MD" },
];

const DEFAULT_PLATFORM = "all";

const DEFAULT_GENRE = "all";
const DEFAULT_SOURCE = "all";

function SearchPool({ onOpen, browse = false, initialSource = DEFAULT_SOURCE }: { onOpen: (item: CatalogItem, el: HTMLElement) => void; browse?: boolean; initialSource?: string }) {
  const catalogRevision = useCatalogRevision();
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState(DEFAULT_PLATFORM);
  const [genre, setGenre] = useState(DEFAULT_GENRE);
  const [source, setSource] = useState(initialSource);
  const [results, setResults] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [total, setTotal] = useState(0);
  const [genres, setGenres] = useState<string[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [retry, setRetry] = useState(0);
  const requestGeneration = useRef(0);
  const moreBusy = useRef(false);
  const searchCardRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const searchInputRef = useRef<HTMLDivElement>(null);
  const dropdown1Ref = useRef<HTMLDivElement>(null);
  const dropdown2Ref = useRef<HTMLDivElement>(null);
  const dropdown3Ref = useRef<HTMLDivElement>(null);

  const fetchPage = useCallback(async (next: number, generation: number) => {
    try {
      const r = await listCatalog({ query: query.trim(), platform, genre, source, page: next, page_size: 50 });
      if (generation !== requestGeneration.current) return;
      if (r?.status !== "success" || !r.data) throw new Error(r?.message || "目录加载失败");
      setResults(prev => next === 1 ? r.data!.items : [...prev, ...r.data!.items]);
      setPage(next); setHasMore(r.data.has_more); setTotal(r.data.total);
      setGenres(r.data.genres || []); setSources(r.data.sources || []);
      setError("");
    } catch (e: any) {
      if (generation === requestGeneration.current) setError(e?.message || "目录加载失败，请重试");
    } finally {
      if (generation === requestGeneration.current) {
        setLoading(false); setLoadingMore(false); moreBusy.current = false;
      }
    }
  }, [query, platform, genre, source]);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    setResults([]); setHasMore(false); setTotal(0); setError("");
    setLoadingMore(false); moreBusy.current = false;
    if (!browse && !query.trim()) { setLoading(false); return; }
    setLoading(true);
    const timer = setTimeout(() => void fetchPage(1, generation), query.trim() ? 350 : 0);
    return () => { clearTimeout(timer); ++requestGeneration.current; };
  }, [fetchPage, browse, retry, catalogRevision]);

  const loadMore = () => {
    if (moreBusy.current || loading || !hasMore) return;
    moreBusy.current = true; setLoadingMore(true);
    void fetchPage(page + 1, requestGeneration.current);
  };

  // 打开卡片：从 refs 中找到对应 DOM 元素传给 onOpen（处理 InstallOverlay 定位）
  const onCardOpen = useCallback((item: CatalogItem) => {
    for (const [idx, el] of searchCardRefs.current) {
      if (el && results[idx]?.game_id === item.game_id) { onOpen(item, el); return; }
    }
  }, [results, onOpen]);

  // ── 导航辅助 ──
  const focusResultCard = useCallback((idx: number) => {
    const wrapper = searchCardRefs.current.get(idx);
    if (!wrapper) return false;
    return focusNavigableElementWithRetry(
      () => wrapper.querySelector(".freedeck-library-grid-card-button") as HTMLElement | null,
    );
  }, []);

  const focusSearchInput = useCallback(() => {
    return focusNavigableElementWithRetry(
      () => searchInputRef.current?.querySelector("input, textarea") as HTMLElement | null,
    );
  }, []);

  const focusDropdown = useCallback((ref: React.RefObject<HTMLDivElement | null>) => {
    return focusNavigableElementWithRetry(
      () => ref.current?.querySelector("button, [tabindex], .Focusable, [class*='Focusable']") as HTMLElement | null,
    );
  }, []);

  const { gridRef, columns } = useCardColumns();
  const cardWidth = 133;

  // 结果网格导航：LEFT/RIGHT 行内移动；UP 第一行→搜索栏；DOWN 跨行
  const onNavigateResults = useCallback((idx: number, btn: number) => {
    const count = results.length;
    if (count === 0) return false;
    if (btn === 11) { // LEFT
      if (idx > 0) return focusResultCard(idx - 1);
      return false;
    }
    if (btn === 12) { // RIGHT
      if (idx + 1 < count) return focusResultCard(idx + 1);
      return false;
    }
    if (btn === 9) { // UP
      if (idx >= columns) return focusResultCard(idx - columns);
      // 第一行 UP → 平台筛选栏
      return focusDropdown(dropdown1Ref);
    }
    if (btn === 10) { // DOWN
      const below = idx + columns;
      if (below < count) return focusResultCard(below);
      return false;
    }
    return false;
  }, [results, columns, focusResultCard, focusDropdown]);

  return (
    <div style={{ minWidth: 0 }}>
      {/* 搜索栏 — TextField */}
      <div ref={searchInputRef} style={{ padding: "12px 0", flexShrink: 0 }}>
        <TextField
          label=""
          description=""
          value={query}
          onChange={(e: any) => setQuery(e?.target?.value || "")}
        />
        {query && <ButtonItem onClick={() => setQuery("")}>清除搜索</ButtonItem>}
      </div>

      {/* 筛选栏 — 平台 / 类型 / 来源 横向排列 */}
      <div style={{ padding: "0 0 10px", flexShrink: 0, display: "flex", gap: 8 }}>
        <div ref={dropdown1Ref} style={{ flex: 1, minWidth: 0 }}>
          <DropdownItem
            label="平台"
            rgOptions={PLATFORM_OPTIONS}
            selectedOption={platform}
            onChange={(opt) => setPlatform(String(opt.data))}
            strDefaultLabel="全部"
          />
        </div>
        <div ref={dropdown2Ref} style={{ flex: 1, minWidth: 0 }}>
          <DropdownItem
            label="类型"
            rgOptions={[{ data: "all", label: "全部" }, ...genres.map(g => ({ data: g, label: g }))]}
            selectedOption={genre}
            onChange={(opt) => setGenre(String(opt.data))}
            strDefaultLabel="全部"
          />
        </div>
        <div ref={dropdown3Ref} style={{ flex: 1, minWidth: 0 }}>
          <DropdownItem
            label="来源"
            rgOptions={[{ data: "all", label: "全部" }, ...sources.map(s => ({ data: s, label: s === "t" ? "天翼云盘" : s }))]}
            selectedOption={source}
            onChange={(opt) => setSource(String(opt.data))}
            strDefaultLabel="全部"
          />
        </div>
      </div>

      <div style={{ fontSize: 12, color: "#8b929a", paddingBottom: 8 }}>共 {total} 款游戏 · 已显示 {results.length} 款</div>
      {error && <div role="alert">{error}<ButtonItem onClick={() => results.length ? loadMore() : setRetry(v => v + 1)}>重试</ButtonItem></div>}
      {/* 结果列表 */}
      <div style={{ paddingBottom: 40 }}>
        {loading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
            <SteamSpinner style={{ width: 32, height: 32 }} />
          </div>
        ) : !browse && !query.trim() ? (
          <div style={{ textAlign: "center", padding: 60, color: "#8b929a", fontSize: 13 }}>
            输入游戏名称开始搜索
          </div>
        ) : results.length === 0 ? (
          <div style={{ textAlign: "center", padding: 60, color: "#8b929a", fontSize: 13 }}>
            未找到匹配的游戏
          </div>
        ) : (
          <div ref={gridRef} style={{
            display: "grid",
            gridTemplateColumns: `repeat(${columns}, ${cardWidth}px)`,
            justifyContent: "center",
            gap: 24,
          }}>
            {results.map((item, i) => (
              <GameCard
                key={item.game_id || i}
                item={item}
                gridIndex={i}
                columns={columns}
                priority={i < 10}
                onNavigate={onNavigateResults}
                onOpen={onCardOpen}
                cardRefs={searchCardRefs}
              />
            ))}
          </div>
        )}
        {hasMore && !loading && <ButtonItem disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : "加载更多"}</ButtonItem>}
      </div>
    </div>
  );
}

function HomeTab({ games, onOpen, initialFocus, onInitialFocused }: {
  games: CatalogItem[];
  onOpen: (item: CatalogItem, el: HTMLElement) => void;
  initialFocus: boolean;
  onInitialFocused: () => void;
}) {
  const catalogRevision = useCatalogRevision();
  const [publicShelves, setPublicShelves] = useState<{ title: string; items: CatalogItem[] }[]>([]);
  const [emuShelves, setEmuShelves] = useState<{ platform: string; title: string; items: CatalogItem[] }[]>([]);
  const shuffled = useMemo(() => [...games].sort(() => Math.random() - 0.5), [games]);
  const shelfRegistry = useRef<Map<number, ShelfRow>>(new Map());

  const allShelves = useMemo(() => {
    const rows = [...publicShelves, { title: "发现游戏", items: shuffled }, ...emuShelves];
    const list = rows.map((row, id) => ({ id, title: row.title, items: row.items }));
    return list;
  }, [shuffled, emuShelves, publicShelves]);

  const focusAt = useCallback((rowId: number, col: number) => {
    const row = shelfRegistry.current.get(rowId);
    if (!row) return false;
    const safe = Math.max(0, Math.min(col, row.items.length - 1));
    const wrapper = row.cardRefs.get(safe);
    if (!wrapper) return false;
    return focusNavigableElementWithRetry(
      () => wrapper.querySelector(".freedeck-library-grid-card-button") as HTMLElement | null,
    );
  }, []);

  // 首次加载自动聚焦第一行第一张卡片
  useEffect(() => {
    if (!initialFocus) return;
    const t = setTimeout(() => {
      focusAt(0, 0);
      onInitialFocused();
    }, 100);
    return () => clearTimeout(t);
  }, [focusAt, initialFocus, onInitialFocused]);

  const makeOnNavigate = useCallback((rowId: number, count: number) => {
    return (idx: number, btn: number) => {
      if (btn === 11) { // LEFT: 行内左移，行首则跳到上一行末尾
        if (idx > 0) return focusAt(rowId, idx - 1);
        if (rowId > 0) {
          const prevCount = shelfRegistry.current.get(rowId - 1)?.items.length ?? 0;
          if (prevCount > 0) return focusAt(rowId - 1, prevCount - 1);
        }
        return false;
      }
      if (btn === 12) { // RIGHT: 行内右移，行尾则跳到下一行开头
        if (idx + 1 < count) return focusAt(rowId, idx + 1);
        const nextRow = shelfRegistry.current.get(rowId + 1);
        if (nextRow && nextRow.items.length > 0) return focusAt(rowId + 1, 0);
        return false;
      }
      if (btn === 9) { // UP
        if (rowId === 0) return false;
        const targetItems = shelfRegistry.current.get(rowId - 1)?.items.length ?? 0;
        if (targetItems === 0) return false;
        return focusAt(rowId - 1, Math.min(idx, targetItems - 1));
      }
      if (btn === 10) { // DOWN
        const target = shelfRegistry.current.get(rowId + 1);
        if (!target || target.items.length === 0) return false;
        return focusAt(rowId + 1, Math.min(idx, target.items.length - 1));
      }
      return false;
    };
  }, [focusAt]);

  const makeOnCardOpen = useCallback((rowId: number) => {
    return (item: CatalogItem) => {
      const row = shelfRegistry.current.get(rowId);
      if (!row) return;
      for (const [idx, wrapper] of row.cardRefs) {
        if (row.items[idx]?.game_id === item.game_id) { onOpen(item, wrapper); return; }
      }
    };
  }, [onOpen]);

  useEffect(() => {
    let active = true;
    const sources = [{ source: "Steam 官方", title: "Steam 官方免费游戏" }, { source: "Flathub", title: "Linux 免费游戏 · Flathub" }];
    void Promise.all(sources.map(async row => {
      try {
        const result = await listCatalog({ source: row.source, page: 1, page_size: 12 });
        return { title: row.title, items: result.data?.items || [] };
      } catch { return { title: row.title, items: [] }; }
    })).then(rows => { if (active) setPublicShelves(rows.filter(row => row.items.length)); });
    return () => { active = false; };
  }, [catalogRevision]);

  useEffect(() => {
    let alive = true;
    const results: typeof emuShelves = [];
    const fetchBatch = async (batch: typeof EMU_SHELVES) => {
      await Promise.all(batch.map(async ({ id, title }) => {
        try {
          const r = await getCuratedShelf({ platform: id, limit: 20 });
          if (r?.status === "success" && r.data?.games?.length && alive) {
            results.push({ platform: id, title, items: r.data.games as CatalogItem[] });
            setEmuShelves([...results].sort((a, b) => EMU_SHELVES.findIndex(p => p.id === a.platform) - EMU_SHELVES.findIndex(p => p.id === b.platform)));
          }
        } catch {}
      }));
    };
    (async () => {
      await fetchBatch(EMU_SHELVES);
    })();
    return () => { alive = false; };
  }, [catalogRevision]);

  return (
    <div style={{ paddingBottom: 40 }}>

      {allShelves.map(shelf => (
        <HomeShelfRow
          key={shelf.id} rowId={shelf.id} title={shelf.title} items={shelf.items}
          registry={shelfRegistry}
          onNavigate={makeOnNavigate(shelf.id, shelf.items.length)}
          onCardOpen={makeOnCardOpen(shelf.id)}
        />
      ))}
    </div>
  );
}

/* ── 单个书架行（注册到全局 registry） ── */
function HomeShelfRow({ rowId, title, items, registry, onNavigate, onCardOpen }: {
  rowId: number; title: string; items: CatalogItem[];
  registry: React.MutableRefObject<Map<number, ShelfRow>>;
  onNavigate: (idx: number, btn: number) => boolean;
  onCardOpen: (item: CatalogItem) => void;
}) {
  const cardRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  // 注册/更新到全局 registry
  useEffect(() => {
    registry.current.set(rowId, { title, items, cardRefs: cardRefs.current });
    return () => { registry.current.delete(rowId); };
  }, [rowId, title, items, registry]);

  return (
    <ShelfView title={title} items={items}
      cardRefs={cardRefs} onNavigate={onNavigate} onOpen={onCardOpen} />
  );
}

/* ═══════════════════════════════════════════
   GridTab — PC 宫格 + 加载更多
   ═══════════════════════════════════════════ */

const GRID_CARD_W = 133;
const GRID_GAP = 24;

function GridTab({ games, hasMore, loadingMore, onLoadMore, onOpen, autoFocusFirst, onAutoFocused }: {
  games: CatalogItem[]; hasMore: boolean; loadingMore: boolean;
  onLoadMore: () => void; onOpen: (item: CatalogItem, el: HTMLElement) => void;
  autoFocusFirst: boolean; onAutoFocused: () => void;
}) {
  const cardRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const { gridRef, columns: GRID_COLS } = useCardColumns();
  const lastFocusedCardIndexRef = useRef(0);
  const pendingRestoreIndexRef = useRef<number | null>(null);
  const gameCount = games.length;
  const LOAD_MORE_IDX = -1;                   // 固定哨兵索引，不受 games 增长影响
  const loadMoreActive = hasMore && !loadingMore;
  const total = gameCount + (loadMoreActive ? 1 : 0);

  // 清理超出范围的卡片 ref（games 增长后旧哨兵已无用）
  useEffect(() => {
    const map = cardRefs.current;
    for (const key of map.keys()) {
      if (key !== LOAD_MORE_IDX && key >= gameCount) map.delete(key);
    }
  }, [gameCount]);

  // 聚焦辅助：rAF 延迟确保 Decky 内部焦点释放后再设置
  const focusEl = useCallback((idx: number) => {
    return focusNavigableElementWithRetry(() => {
      const wrapper = cardRefs.current.get(idx);
      return wrapper?.querySelector(".freedeck-library-grid-card-button") as HTMLElement | null;
    });
  }, []);

  const handleCardFocus = useCallback((idx: number) => {
    lastFocusedCardIndexRef.current = idx;
  }, []);

  const restoreFocusedCard = useCallback((idx: number | null) => {
    if (idx == null || gameCount <= 0) return false;
    const safe = Math.max(0, Math.min(idx, gameCount - 1));
    lastFocusedCardIndexRef.current = safe;
    return focusEl(safe);
  }, [focusEl, gameCount]);

  // Tab 切换后聚焦第一个游戏卡片
  useEffect(() => {
    if (!autoFocusFirst || gameCount === 0 || loadingMore) return;
    const t = setTimeout(() => {
      if (focusEl(0)) onAutoFocused();
    }, 100);
    return () => clearTimeout(t);
  }, [autoFocusFirst, gameCount, loadingMore, focusEl, onAutoFocused]);

  const handleLoadMore = useCallback(() => {
    if (loadingMore || !hasMore) return;
    // 加载后焦点跳到第一张新卡片
    pendingRestoreIndexRef.current = gameCount;
    onLoadMore();
  }, [gameCount, hasMore, loadingMore, onLoadMore]);

  useEffect(() => {
    if (loadingMore) return;
    if (pendingRestoreIndexRef.current == null) return;
    const restoreIndex = pendingRestoreIndexRef.current;
    pendingRestoreIndexRef.current = null;
    restoreFocusedCard(restoreIndex);
  }, [games.length, loadingMore, restoreFocusedCard]);

  // 统一导航（游戏卡片 + 加载更多按钮为同一焦点组）
  const onNavigate = useCallback((idx: number, btn: number) => {
    // ── 当前焦点在「加载更多」按钮上 ──
    if (idx === LOAD_MORE_IDX) {
      if (btn === 9) { // UP → 跳回最后一行最后一个卡片
        const lastCard = gameCount - 1;
        if (lastCard >= 0) { restoreFocusedCard(lastCard); return true; }
        return false;
      }
      // LEFT(11) / RIGHT(12) / DOWN(10): 焦点停留
      return false;
    }

    // ── 当前焦点在游戏卡片上 ──
    const row = Math.floor(idx / GRID_COLS);
    const col = idx % GRID_COLS;
    let next = -1;

    if (btn === 11) { // LEFT
      if (col > 0) next = idx - 1;
    } else if (btn === 12) { // RIGHT
      if (col < GRID_COLS - 1 && idx + 1 < gameCount) next = idx + 1;
    } else if (btn === 9) { // UP
      if (row > 0) next = idx - GRID_COLS;
    } else if (btn === 10) { // DOWN
      const below = idx + GRID_COLS;
      if (below < gameCount) {
        next = below;
      } else if (loadMoreActive) {
        lastFocusedCardIndexRef.current = idx;
        focusEl(LOAD_MORE_IDX);
        return true;
      }
    }

    if (next >= 0 && next < gameCount) {
      focusEl(next);
      return true;
    }
    return false;
  }, [gameCount, GRID_COLS, loadMoreActive, focusEl]);

  const onCardOpen = useCallback((item: CatalogItem) => {
    for (const [idx, el] of cardRefs.current)
      if (el && games[idx]?.game_id === item.game_id) { onOpen(item, el); return; }
  }, [games, onOpen]);



  if (total === 0) return <div style={{ display:"flex",justifyContent:"center",padding:60,color:"#8b929a",fontSize:13 }}>暂无游戏</div>;

  return (
    <div ref={gridRef} style={{
      display: "grid",
      gridTemplateColumns: `repeat(${GRID_COLS}, ${GRID_CARD_W}px)`,
      columnGap: GRID_GAP, rowGap: 37, justifyContent: "center",
      padding: "20px 0 40px",
    }}>
      {games.map((item, i) => (
        <GameCard key={item.game_id || i} item={item} gridIndex={i}
          columns={GRID_COLS} priority={i < 20}
          onNavigate={onNavigate} onOpen={onCardOpen} cardRefs={cardRefs}
          onFocusIndex={handleCardFocus} />
      ))}
      {hasMore && (
        <div
          ref={(el) => { if (el) cardRefs.current.set(LOAD_MORE_IDX, el); }}
          className="freedeck-library-load-more-card"
          style={{ width: GRID_CARD_W, height: 200 }}
        >
          <Focusable
            className="freedeck-library-grid-card-button"
            onOKButton={loadingMore ? undefined : handleLoadMore}
            onOKActionDescription={loadingMore ? undefined : "加载更多"}
            onGamepadDirection={(e: CustomEvent) => {
              const btn = Number(e?.detail?.button ?? -1);
              // UP → 回到最后一行最后一个卡片
              if (btn === 9) {
                const last = gameCount - 1;
                if (last >= 0) {
                  restoreFocusedCard(last);
                  e.preventDefault?.();
                }
              }
              // LEFT/RIGHT/DOWN: 焦点停留
            }}
            style={{
              width: "100%", height: 200, minHeight: 200, maxHeight: 200,
              padding: 0, borderRadius: 0, border: "none",
              background: "transparent", display: "block",
              pointerEvents: loadingMore ? "none" : "auto",
              opacity: loadingMore ? 0.6 : 1,
        } as any}
          >
            <div className="freedeck-library-grid-card-frame freedeck-library-load-more-card-frame"
              style={{ width: "100%", height: "100%" }}>
              <div className="freedeck-library-load-more-card-inner">
                <div className="freedeck-library-load-more-card-title">
                  {loadingMore ? "加载中..." : "加载更多"}
                </div>
                <div className="freedeck-library-load-more-card-subtitle">
                  {loadingMore ? "请稍候" : "继续获取更多资源"}
                </div>
              </div>
            </div>
          </Focusable>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════
   PlayLaterTab — 稍后再玩网格 + 移除
   ═══════════════════════════════════════════ */

function PlayLaterTab({ onOpen }: { onOpen: (item: CatalogItem, el: HTMLElement) => void }) {
  const { gridRef, columns: GRID_COLS } = useCardColumns();
  const [items, setItems] = useState<PlayLaterItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [removingId, setRemovingId] = useState("");
  const cardRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const focusedRef = useRef(false);

  // 挂载时加载稍后再玩列表
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const r = await playLaterList();
        if (alive && r?.status === "success") setItems((r.data?.items || []) as PlayLaterItem[]);
      } catch {} finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  // 首次有数据时聚焦第一张卡片
  useEffect(() => {
    if (focusedRef.current || items.length === 0) return;
    focusedRef.current = true;
    const t = setTimeout(() => {
      const wrapper = cardRefs.current.get(0);
      focusNavigableElementWithRetry(
        () => wrapper?.querySelector(".freedeck-library-grid-card-button") as HTMLElement | null,
      );
    }, 150);
    return () => clearTimeout(t);
  }, [items.length]);

  const handleRemove = useCallback(async (item: PlayLaterItem) => {
    const itemId = String(item.play_later_id || "").trim();
    if (!itemId || removingId) return;
    setRemovingId(itemId);
    try {
      const r = await playLaterRemove({ item_id: itemId });
      if (r?.status !== "success") throw new Error(r?.message || "移除稍后再玩失败");
      if (!r.data?.removed) throw new Error(r.data?.message || "稍后再玩条目不存在");
      setItems(prev => prev.filter(it => it.play_later_id !== itemId));
      toaster.toast({ title: "已移除稍后再玩", body: item.title_zh || item.title || "", duration: 2400 });
    } catch (e: any) {
      toaster.toast({ title: "稍后再玩", body: String(e?.message || e), duration: 4000 });
    } finally {
      setRemovingId("");
    }
  }, [removingId]);

  const focusCard = useCallback((idx: number) => {
    const wrapper = cardRefs.current.get(idx);
    if (!wrapper) return false;
    return focusNavigableElementWithRetry(
      () => wrapper.querySelector(".freedeck-library-grid-card-button") as HTMLElement | null,
    );
  }, []);

  const onNavigate = useCallback((idx: number, btn: number) => {
    const count = items.length;
    const row = Math.floor(idx / GRID_COLS);
    const col = idx % GRID_COLS;
    let next = -1;
    if (btn === 11) { if (col > 0) next = idx - 1; }
    else if (btn === 12) { if (col < GRID_COLS - 1 && idx + 1 < count) next = idx + 1; }
    else if (btn === 9) { if (row > 0) next = idx - GRID_COLS; }
    else if (btn === 10) { if (idx + GRID_COLS < count) next = idx + GRID_COLS; }
    if (next >= 0 && next < count) return focusCard(next);
    return false;
  }, [items.length, GRID_COLS, focusCard]);

  const onCardOpen = useCallback((item: CatalogItem) => {
    const targetId = (item as PlayLaterItem).play_later_id;
    for (const [idx, el] of cardRefs.current)
      if (el && items[idx]?.play_later_id === targetId) { onOpen(item, el); return; }
  }, [items, onOpen]);



  if (loading) {
    return <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
      <SteamSpinner style={{ width: 40, height: 40 }} />
    </div>;
  }

  if (items.length === 0) {
    return (
      <div style={{ textAlign: "center", padding: "80px 40px", color: "#8b929a", fontSize: 13 }}>
        还没有加入任何“稍后再玩”游戏。
      </div>
    );
  }

  return (
    <div ref={gridRef} style={{
      display: "grid",
      gridTemplateColumns: `repeat(${GRID_COLS}, ${GRID_CARD_W}px)`,
      columnGap: GRID_GAP, rowGap: 37, justifyContent: "center",
      padding: "20px 0 40px",
    }}>
      {items.map((item, i) => (
        <PlayLaterCard key={item.play_later_id || item.game_id || i}
          item={item} gridIndex={i} columns={GRID_COLS} priority={i < 20}
          onNavigate={onNavigate} onOpen={onCardOpen} cardRefs={cardRefs}
          removing={removingId === item.play_later_id}
          onRemove={() => handleRemove(item)} />
      ))}
    </div>
  );
}

/* ── 稍后再玩卡片（GameCard 结构 + ✕ 移除） ── */

function PlayLaterCard(props: {
  item: PlayLaterItem;
  gridIndex: number;
  columns: number;
  priority: boolean;
  onNavigate: (idx: number, button: number) => boolean;
  onOpen: (item: CatalogItem) => void;
  cardRefs: React.MutableRefObject<Map<number, HTMLDivElement>>;
  onRemove: () => void;
  removing: boolean;
}) {
  const { item, gridIndex, columns, priority, onNavigate, onOpen, cardRefs, onRemove, removing } = props;
  const [sideLeft, setSideLeft] = useState(false);
  const [imgErr, setImgErr] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const cover = !imgErr ? (item.cover_url || "") : "";
  const title = item.title_zh || item.title || "";
  const subtitle = title !== item.title_en && item.title_en ? item.title_en : "";
  const size = item.size_text || "";
  const preloadedRef = useRef(false);
  const onFocus = useCallback(() => {
    setSideLeft(gridIndex % columns === columns - 1);
    if (!preloadedRef.current && cover) {
      preloadedRef.current = true;
      const link = document.createElement("link");
      link.rel = "preload";
      link.as = "image";
      link.href = cover;
      document.head.appendChild(link);
    }
  }, [columns, gridIndex, cover]);

  useEffect(() => {
    if (rootRef.current) cardRefs.current.set(gridIndex, rootRef.current);
    return () => { cardRefs.current.delete(gridIndex); };
  }, [gridIndex, cardRefs]);

  return (
    <div
      ref={rootRef}
      className="freedeck-library-grid-card"
      onFocusCapture={onFocus}
      onMouseEnter={onFocus}
      style={{ position: "relative" }}
    >
      <Focusable
        className="freedeck-library-grid-card-button"
        focusClassName={getLibraryFocusClassName() || undefined}
        onFocus={() => { onFocus(); }}
        onBlur={() => {}}
        onOKButton={() => onOpen(item)}
        onClick={() => onOpen(item)}
        onOKActionDescription="查看"
        onSecondaryActionDescription={removing ? "移除中…" : "移除稍后再玩"}
        onSecondaryButton={() => { if (!removing) onRemove(); }}
        onGamepadDirection={(e: CustomEvent) => {
          const btn = Number(e?.detail?.button ?? -1);
          if (btn >= 0 && onNavigate(gridIndex, btn)) {
            e.preventDefault?.();
            e.stopPropagation?.();
          }
        }}
        style={{
          width: "100%", height: "200px", minHeight: "200px", maxHeight: "200px",
          padding: "0", overflow: "visible", borderRadius: "0", border: "none",
          background: "transparent", boxShadow: "none", display: "block",
          cursor: "pointer", textAlign: "left",
          transition: "background 210ms ease",
        } as any}
      >
        <div className="freedeck-library-grid-card-frame">
          {/* 封面 */}
          <div className="freedeck-library-grid-card-cover-wrap">
            {cover ? (
              <img
                className="freedeck-library-grid-card-cover"
                src={cover}
                alt={title}
                loading={priority ? "eager" : "lazy"}
                decoding="async"
                referrerPolicy="no-referrer"
                onError={() => setImgErr(true)}
              />
            ) : null}
          </div>

          {/* 侧边信息 */}
          <div className={`freedeck-library-grid-card-sideinfo${sideLeft ? " is-sideinfo-left" : ""}`}>
            <div className="freedeck-library-grid-card-sideinfo-title">{title || "-"}</div>
            {subtitle && <div className="freedeck-library-grid-card-sideinfo-subtitle">{subtitle}</div>}
            {size && <div className="freedeck-library-grid-card-sideinfo-size">存储空间 {size}</div>}
          </div>
        </div>
      </Focusable>
    </div>
  );
}

const NF_CSS = `
@keyframes nf-loading-bar {
  0% { opacity:0; transform:translateX(-100%); }
  50% { opacity:1; }
  100% { opacity:0; transform:translateX(100%); }
}
`;
