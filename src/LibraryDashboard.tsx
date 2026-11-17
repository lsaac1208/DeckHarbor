import { ActionButton as ButtonItem, SelectField as DropdownItem, UIStyles, PageHeader } from "./ui";
import { TextField, Focusable, SteamSpinner, Navigation, showModal, ConfirmModal } from "@decky/ui";
import { toaster } from "@decky/api";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { libraryDashboard, libraryFavorite, libraryForget, uninstallGame } from "./api";
import type { LibraryGame } from "./api";
import { launchLibraryGame, announceLibraryChange, requireData } from "./steamLaunch";
import { selectLibraryGames, formatPlaytime } from "./libraryModel";
import type { LibraryFilter } from "./libraryModel";
import { EMU_MANAGER_ROUTE, LIBRARY_ROUTE } from "./routes";

export default function LibraryDashboard({ compact = false }: { compact?: boolean }) {
  const [games, setGames] = useState<LibraryGame[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<LibraryFilter>("all");
  const [platform, setPlatform] = useState("all");
  const [sort, setSort] = useState("recent");
  const [showPlaytime, setShowPlaytime] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const generation = useRef(0);
  const loadBusy = useRef(false);
  const actionBusy = useRef(false);
  const load = useCallback(async () => {
    if (loadBusy.current) return;
    loadBusy.current = true;
    const token = generation.current;
    try {
      const data = requireData(await libraryDashboard());
      if (token !== generation.current) return;
      setGames(data.games); setShowPlaytime(data.show_playtime); setError("");
    } catch (e: any) { if (token === generation.current) setError(e?.message || "读取游戏库失败"); }
    finally { loadBusy.current = false; if (token === generation.current) setLoading(false); }
  }, []);
  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), 10000);
    const changed = () => void load();
    window.addEventListener("newfreedeck-installed-changed", changed);
    window.addEventListener("newfreedeck-settings-changed", changed);
    return () => { ++generation.current; clearInterval(interval); window.removeEventListener("newfreedeck-installed-changed", changed); window.removeEventListener("newfreedeck-settings-changed", changed); };
  }, [load]);
  const visible = useMemo(() => selectLibraryGames(games, query, filter, platform, sort), [games, query, filter, platform, sort]);
  const platforms = useMemo(() => [...new Set(games.filter(g => !g.game_id.endsWith("_emulator")).map(g => g.platform || "pc"))].sort(), [games]);
  const act = async (game: LibraryGame, operation: () => Promise<void>) => {
    if (actionBusy.current) return;
    actionBusy.current = true; setBusy(game.game_id);
    try { await operation(); await load(); }
    catch (e: any) { toaster.toast({ title: game.title || "游戏库", body: e?.message || "操作失败" }); }
    finally { actionBusy.current = false; setBusy(null); }
  };
  const remove = (game: LibraryGame, files: boolean) => {
    const modal = showModal(<ConfirmModal strTitle={files ? "卸载游戏" : "移除游戏记录"}
      strDescription={files ? `删除「${game.title}」的游戏安装目录及快捷方式。安装目录内的存档也会被删除，请先备份。` : `从插件和 Steam 库中移除「${game.title}」，保留原文件与存档。`}
      strOKButtonText={files ? "卸载" : "移除"} strCancelButtonText="保留"
      onCancel={() => modal.Close()} onOK={() => { modal.Close(); void act(game, async () => {
        requireData(await (files ? uninstallGame : libraryForget)({ game_id: game.game_id }));
        if (game.steam_appid) {
          try {
            const apps = (window as any).SteamClient?.Apps;
            if (!apps?.RemoveShortcut) throw new Error("Steam API unavailable");
            await apps.RemoveShortcut(game.steam_appid >>> 0);
          }
          catch { toaster.toast({ title: "记录已移除", body: "Steam 快捷方式请在 Steam 库中手动移除" }); }
        }
        announceLibraryChange();
      }); }} />);
  };
  return <div className={`nfd-ui nfd-dashboard${compact ? " nfd-qam-library" : ""}`}>
    <UIStyles />
    <PageHeader title="我的游戏" subtitle={`${games.filter(g => !g.game_id.endsWith("_emulator")).length} 款游戏 · ${games.filter(g => g.running).length} 款运行中`}
      actions={<><ButtonItem onClick={() => Navigation.Navigate(EMU_MANAGER_ROUTE)}>模拟器</ButtonItem><ButtonItem onClick={() => Navigation.Navigate(LIBRARY_ROUTE)}>发现游戏</ButtonItem><ButtonItem onClick={() => void load()}>刷新</ButtonItem></>} />
    <div className="nfd-search"><TextField label="搜索已安装游戏" value={query} onChange={e => setQuery(e.target.value)} /></div>
    <div className="nfd-library-filters">
      <DropdownItem label="视图" selectedOption={filter} rgOptions={[{ data: "all", label: "全部游戏" }, { data: "recent", label: "最近游玩" }, { data: "favorites", label: "我的收藏" }, { data: "running", label: "正在运行" }]} onChange={o => setFilter(o.data as LibraryFilter)} />
      <DropdownItem label="平台" selectedOption={platform} rgOptions={[{ data: "all", label: "全部平台" }, ...platforms.map(p => ({ data: p, label: p.toUpperCase() }))]} onChange={o => setPlatform(String(o.data))} />
      <DropdownItem label="排序" selectedOption={sort} rgOptions={[{ data: "recent", label: "最近游玩" }, { data: "title", label: "游戏名称" }, { data: "playtime", label: "游玩时长" }, { data: "installed", label: "安装时间" }]} onChange={o => setSort(String(o.data))} />
    </div>
    {error && <div role="alert" className="nfd-message is-error">{error}<ButtonItem onClick={() => void load()}>重试</ButtonItem></div>}
    {loading ? <SteamSpinner style={{ width: 36, height: 36, margin: "40px auto" }} /> : !visible.length ? <div className="nfd-empty">没有符合条件的游戏。可浏览资源库安装游戏，或在模拟器管理中导入本地 ROM。</div> : null}
    <div className="nfd-library-items">
      {visible.map(game => <div key={game.game_id} className="nfd-library-item">
        <div className="nfd-game-summary">
          {game.cover_url ? <img className="nfd-game-cover" src={game.cover_url} alt="" loading="lazy" /> : <div className="nfd-game-cover">{(game.platform || "PC").toUpperCase()}</div>}
          <div className="nfd-game-copy"><div className="nfd-game-title">{game.title || game.game_id}</div><div className="nfd-muted">{game.running ? "正在运行" : game.available === false ? "文件已移动或存储设备未连接" : (game.platform || "PC").toUpperCase()}{game.favorite ? " · ★ 收藏" : ""}</div>
            {showPlaytime && <div className="nfd-muted">游玩 {formatPlaytime(game.playtime_seconds)}</div>}
            <div className="nfd-muted">{game.last_played ? `最近：${new Date(game.last_played * 1000).toLocaleDateString()}` : "还没有游玩记录"}</div>
          </div>
        </div>
        <div className="nfd-game-actions">
          <ButtonItem className="is-primary" disabled={!!busy || game.running || game.available === false} onClick={() => void act(game, () => launchLibraryGame(game.game_id))}>{busy === game.game_id ? "处理中…" : game.running ? "运行中" : "启动"}</ButtonItem>
          <ButtonItem disabled={!!busy} onClick={() => void act(game, async () => { requireData(await libraryFavorite({ game_id: game.game_id, favorite: !game.favorite })); })}>{game.favorite ? "取消收藏" : "收藏"}</ButtonItem>
          <ButtonItem disabled={!!busy || !game.steam_appid} onClick={() => Navigation.Navigate(`/library/app/${game.steam_appid >>> 0}`)}>Steam 详情</ButtonItem>
          <ButtonItem disabled={!!busy || game.running} onClick={() => remove(game, false)}>移除记录</ButtonItem>
          {game.managed_files !== false && <ButtonItem disabled={!!busy || game.running || game.available === false} onClick={() => remove(game, true)}>卸载文件</ButtonItem>}
        </div>
      </div>)}
    </div>
    {showPlaytime && <p className="nfd-muted" style={{ marginTop: 20 }}>游玩时长从本版本启用后累计，约每 10 秒更新；设备休眠期间不计时。</p>}
  </div>;
}
