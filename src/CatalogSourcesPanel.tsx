import { useCallback, useEffect, useState } from "react";
import { getCatalogSources, updateCatalog, type CatalogSource } from "./api";
import { useCatalogRevision } from "./catalogUpdates";
import { ActionButton, PageHeader, Section } from "./ui";

export default function CatalogSourcesPanel({ onBrowse }: { onBrowse?: (source: string) => void }) {
  const revision = useCatalogRevision();
  const [sources, setSources] = useState<CatalogSource[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const load = useCallback(async () => {
    const response = await getCatalogSources();
    if (response.status !== "success" || !response.data) throw new Error(response.message || "无法读取来源状态");
    setSources(response.data.sources); setError("");
  }, []);
  useEffect(() => {
    let active = true;
    const read = () => { if (active) void load().catch(e => active && setError(String(e.message || e))); };
    read(); const interval = setInterval(read, 5000);
    return () => { active = false; clearInterval(interval); };
  }, [load, revision]);
  const refresh = async (source: string) => {
    setBusy(source); setError("");
    try {
      const response = await updateCatalog({ source });
      if (response.status !== "success") throw new Error(response.message || "刷新失败");
      setMessage("正在后台更新，已有游戏仍可浏览。"); await load();
    } catch (e: any) { setError(e.message || String(e)); }
    finally { setBusy(""); }
  };
  return <div><PageHeader title="游戏来源" subtitle="先显示本地目录，再在后台更新；一个来源不可用不会挡住其他游戏。" />
    {error && <div className="nfd-message is-error" role="alert">{error}<ActionButton onClick={() => void load().catch(e => setError(String(e)))}>重试</ActionButton></div>}
    {message && <p className="nfd-message">{message}</p>}
    {!sources.length && !error && <p className="nfd-muted">读取来源…</p>}
    {sources.map(source => <Section key={source.id} title={source.name}>
      <p>{source.count} 个目录条目 · {source.refreshing ? "后台更新中" : source.error ? "显示本地内容" : "可浏览"}</p>
      <p className="nfd-muted">{source.description}</p>
      <p className="nfd-muted">{source.updated_at ? `上次更新：${new Date(source.updated_at * 1000).toLocaleString()}` : "使用随插件提供或已有的本地目录"}</p>
      {source.error && <p className="nfd-muted">{source.error}</p>}
      {source.id !== "legacy" && onBrowse && <ActionButton onClick={() => onBrowse(({ flathub: "Flathub", steam: "Steam 官方", homebrew: "4TU Homebrew" } as Record<string, string>)[source.id])}>浏览此来源的游戏</ActionButton>}
      <ActionButton disabled={!!busy || source.refreshing} onClick={() => void refresh(source.id)}>更新此来源</ActionButton>
    </Section>)}
  </div>;
}
