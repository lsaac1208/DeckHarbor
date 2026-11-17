import type { LibraryGame } from "./api";
export type LibraryFilter = "all" | "recent" | "favorites" | "running";
export function selectLibraryGames(games: LibraryGame[], query: string, filter: LibraryFilter, platform: string, sort: string): LibraryGame[] {
  const q = query.trim().toLocaleLowerCase();
  return games.filter(g => !g.game_id.endsWith("_emulator"))
    .filter(g => !q || (g.title || "").toLocaleLowerCase().includes(q))
    .filter(g => platform === "all" || (g.platform || "pc") === platform)
    .filter(g => filter === "all" || (filter === "favorites" ? g.favorite : filter === "running" ? g.running : g.last_played > 0))
    .sort((a, b) => {
      if (sort === "title") return (a.title || "").localeCompare(b.title || "", "zh");
      if (sort === "playtime") return b.playtime_seconds - a.playtime_seconds;
      if (sort === "installed") return Date.parse(b.installed_at || "1970-01-01") - Date.parse(a.installed_at || "1970-01-01");
      return Number(b.running) - Number(a.running) || b.last_played - a.last_played || (a.title || "").localeCompare(b.title || "", "zh");
    });
}
export function formatPlaytime(seconds: number): string {
  if (seconds < 60) return seconds > 0 ? "不足 1 分钟" : "尚未记录";
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}
