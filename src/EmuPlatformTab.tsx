import { useCatalogRevision } from "./catalogUpdates";
import { useCardColumns, ActionButton as ButtonItem } from "./ui";
/* ═══════════════════════════════════════════
   NewFreedeck — 模拟器平台游戏网格
   ═══════════════════════════════════════════ */

import { SteamSpinner, Focusable } from "@decky/ui";
import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { getPlatformGames } from "./api";
import { GameCard, getLibraryFocusClassName } from "./library/GameCard";
import { focusNavigableElementWithRetry } from "./library/focus";
import type { CatalogItem } from "./api";

const GRID_CARD_W = 133;
const GRID_GAP = 24;
const PAGE_SIZE = 25;

export const EMU_PLATFORM_CSS = `
.freedeck-emu-platform-grid {
  width: 100%;
}
.freedeck-emu-platform-grid .freedeck-emu-platform-grid-stage {
  width: 100%;
  max-width: 806px;
  margin: 0 auto;
  padding: 0 4px 20px;
  box-sizing: border-box;
}
`;

const LOAD_MORE_IDX = -1;

export function EmuPlatformGrid({ platform, onOpen }: {
  platform: string;
  onOpen: (item: CatalogItem, el: HTMLElement) => void;
}) {
  const catalogRevision = useCatalogRevision();
  const { gridRef, columns: GRID_COLS } = useCardColumns();
  const [games, setGames] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const busyRef = useRef(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const cardRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const platformCacheRef = useRef<Map<string, { games: CatalogItem[]; total: number; page: number; hasMore: boolean }>>(new Map());
  const pendingRestoreIndexRef = useRef<number | null>(null);

  const gameCount = games.length;
  const loadMoreActive = hasMore && !loadingMore;
  const total = gameCount + (loadMoreActive ? 1 : 0);

  useEffect(() => {
    platformCacheRef.current.clear();
    setRevision(value => value + 1);
  }, [catalogRevision]);

  // 聚焦辅助
  const focusEl = useCallback((idx: number) => {
    return focusNavigableElementWithRetry(() => {
      const wrapper = cardRefs.current.get(idx);
      return wrapper?.querySelector(".freedeck-library-grid-card-button") as HTMLElement | null;
    });
  }, []);

  const restoreFocusedCard = useCallback((idx: number | null) => {
    if (idx == null || gameCount <= 0) return false;
    const safe = Math.max(0, Math.min(idx, gameCount - 1));
    return focusEl(safe);
  }, [focusEl, gameCount]);

  useEffect(() => {
    const token = ++generation.current;
    busyRef.current = false; setLoadingMore(false); setError("");
    const cached = platformCacheRef.current.get(platform);
    if (cached) {
      setGames(cached.games); setHasMore(cached.hasMore); setPage(cached.page); setLoading(false);
      return () => { ++generation.current; };
    }
    setPage(1); setHasMore(false); setLoading(true);
    getPlatformGames({ platform, page: 1, page_size: PAGE_SIZE }).then(r => {
      if (token !== generation.current) return;
      if (r.status !== "success" || !r.data) throw new Error(r.message || "目录加载失败");
      const data = r.data;
      platformCacheRef.current.set(platform, { games: data.games, total: data.total, page: 1, hasMore: data.has_more });
      setGames(data.games); setHasMore(data.has_more);
    }).catch(e => { if (token === generation.current) setError(e?.message || "加载失败"); })
      .finally(() => { if (token === generation.current) setLoading(false); });
    return () => { ++generation.current; };
  }, [platform, revision]);

  const loadMore = useCallback(async () => {
    if (busyRef.current || !hasMore) return;
    const token = generation.current;
    busyRef.current = true; setLoadingMore(true); setError("");
    const next = page + 1;
    try {
      const r = await getPlatformGames({ platform, page: next, page_size: PAGE_SIZE });
      if (token !== generation.current) return;
      if (r.status !== "success" || !r.data) throw new Error(r.message || "加载更多失败");
      const merged = [...games, ...r.data.games];
      if (r.data.games.length) pendingRestoreIndexRef.current = games.length;
      platformCacheRef.current.set(platform, { games: merged, total: r.data.total, page: next, hasMore: r.data.has_more });
      setGames(merged); setPage(next); setHasMore(r.data.has_more);
    } catch (e: any) { if (token === generation.current) setError(e?.message || "加载更多失败"); }
    finally { if (token === generation.current) { busyRef.current = false; setLoadingMore(false); } }
  }, [page, hasMore, platform, games]);

  // 加载完成后恢复焦点到第一张新卡片
  useEffect(() => {
    if (loadingMore) return;
    if (pendingRestoreIndexRef.current == null) return;
    const restoreIndex = pendingRestoreIndexRef.current;
    pendingRestoreIndexRef.current = null;
    restoreFocusedCard(restoreIndex);
  }, [games.length, loadingMore, restoreFocusedCard]);

  const onNavigate = useCallback((idx: number, btn: number) => {
    // ── 当前焦点在「加载更多」按钮上 ──
    if (idx === LOAD_MORE_IDX) {
      if (btn === 9) { // UP → 跳回最后一行最后一个卡片
        const lastCard = gameCount - 1;
        if (lastCard >= 0) { focusEl(lastCard); return true; }
        return false;
      }
      return false; // LEFT/RIGHT/DOWN: 焦点停留
    }

    // ── 当前焦点在游戏卡片上 ──
    const count = gameCount;
    if (count === 0) return false;
    const row = Math.floor(idx / GRID_COLS);
    const col = idx % GRID_COLS;
    let next = -1;

    if (btn === 11) { // LEFT
      if (col > 0) next = idx - 1;
    } else if (btn === 12) { // RIGHT
      if (col < GRID_COLS - 1 && idx + 1 < count) next = idx + 1;
    } else if (btn === 9) { // UP
      if (row > 0) next = idx - GRID_COLS;
    } else if (btn === 10) { // DOWN
      const below = idx + GRID_COLS;
      if (below < count) {
        next = below;
      } else if (loadMoreActive) {
        focusEl(LOAD_MORE_IDX);
        return true;
      }
    }

    if (next >= 0 && next < count) {
      focusEl(next);
      return true;
    }
    return false;
  }, [gameCount, GRID_COLS, loadMoreActive, focusEl]);

  const onCardOpen = useCallback((item: CatalogItem) => {
    for (const [idx, el] of cardRefs.current)
      if (el && games[idx]?.game_id === item.game_id) { onOpen(item, el); return; }
  }, [games, onOpen]);



  if (loading && !games.length) return (
    <div className="freedeck-emu-platform-grid">
      {platform === "switch" && <p className="nfd-muted">含 Switch 自制游戏；获取方式与兼容性请查看游戏详情。</p>}
      <div style={{ display:"flex",justifyContent:"center",padding:60 }}>
        <SteamSpinner style={{width:40,height:40}} />
      </div>
    </div>
  );

  if (error && games.length === 0) return <div role="alert" style={{ padding: 40 }}>{error}
    <ButtonItem onClick={() => { platformCacheRef.current.delete(platform); setRevision(v => v + 1); }}>重试</ButtonItem>
  </div>;

  if (total === 0) return (
    <div className="freedeck-emu-platform-grid">
      {platform === "switch" && <p className="nfd-muted">含 Switch 自制游戏；获取方式与兼容性请查看游戏详情。</p>}
      <div style={{ display:"flex",justifyContent:"center",padding:60,color:"#8b929a",fontSize:13 }}>暂无游戏</div>
    </div>
  );

  const focusClass = getLibraryFocusClassName();

  return (
    <div className="freedeck-emu-platform-grid">
      {platform === "switch" && <p className="nfd-muted">含 Switch 自制游戏；获取方式与兼容性请查看游戏详情。</p>}
      {error && <div role="alert">{error}<ButtonItem onClick={loadMore}>重试加载更多</ButtonItem></div>}
      <div className="freedeck-emu-platform-grid-stage">
        <div ref={gridRef} style={{
          display: "grid",
          gridTemplateColumns: `repeat(${GRID_COLS}, ${GRID_CARD_W}px)`,
          columnGap: GRID_GAP, rowGap: 37, justifyContent: "center",
          padding: "20px 0 40px",
        }}>
          {games.map((item, i) => (
            <GameCard key={item.game_id || i} item={item} gridIndex={i}
              columns={GRID_COLS} priority={i < 20}
              onNavigate={onNavigate} onOpen={onCardOpen} cardRefs={cardRefs} />
          ))}
          {hasMore && (
            <div
              ref={(el) => { if (el) cardRefs.current.set(LOAD_MORE_IDX, el); }}
              className="freedeck-library-grid-card freedeck-library-load-more-card"
              style={{ position: "relative", width: GRID_CARD_W, height: 200 }}
            >
              <Focusable
                className="freedeck-library-grid-card-button"
                focusClassName={focusClass || undefined}
                onClick={loadingMore ? undefined : loadMore}
                onOKButton={loadingMore ? undefined : loadMore}
                onOKActionDescription={loadingMore ? undefined : "加载更多"}
                onGamepadDirection={(e: CustomEvent) => {
                  const btn = Number(e?.detail?.button ?? -1);
                  if (btn === 9) {
                    const last = gameCount - 1;
                    if (last >= 0) {
                      focusEl(last);
                      e.preventDefault?.();
                    }
                  }
                }}
                style={{
                  width: "100%", height: "200px", minHeight: "200px", maxHeight: "200px",
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
      </div>
    </div>
  );
}