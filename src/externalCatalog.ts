import type { CatalogItem } from "./api";
export function externalGameUrl(game: Pick<CatalogItem, "acquisition" | "action_url">): string {
  if (game.acquisition !== "external") return "";
  try {
    const url = new URL(game.action_url || "");
    return url.protocol === "https:" && !url.username && !url.password ? url.href : "";
  } catch { return ""; }
}
