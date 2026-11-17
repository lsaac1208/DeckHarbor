import { Focusable, Navigation } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { playLaterAdd, type CatalogItem } from "../api";
import { externalGameUrl } from "../externalCatalog";
import { ActionButton, UIStyles } from "../ui";

export function ExternalGameDetails({ game, onClose }: { game: CatalogItem; onClose: () => void }) {
  const firstButton = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const url = externalGameUrl(game);
  useEffect(() => { const timer = setTimeout(() => firstButton.current?.focus(), 60); return () => clearTimeout(timer); }, []);
  async function save() {
    setSaving(true);
    try {
      const result = await playLaterAdd({ item: game });
      if (result.status !== "success") throw new Error(result.message || "保存失败");
      setMessage("已加入稍后再玩");
    } catch (e: any) { setMessage(e.message || String(e)); }
    finally { setSaving(false); }
  }
  return <Focusable className="nfd-ui nfd-external-overlay" role="dialog" aria-modal="true" aria-label={game.title} onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose(); }} onCancelActionDescription="返回">
    <UIStyles /><div className="nfd-external-dialog">
      <div>{game.cover_url && <img className="nfd-external-cover" src={game.cover_url} alt="" />}<h2>{game.title_zh || game.title}</h2><p className="nfd-muted">{game.source_name} · {game.platform.toUpperCase()}</p></div>
      <div className="nfd-external-copy"><p>{game.summary}</p><p className="nfd-message">{game.acquisition_note}</p>
        {game.license && <p className="nfd-muted">许可：{game.license}{game.creator ? ` · 开发者：${game.creator}` : ""}</p>}
        {message && <p role="status" className="nfd-message">{message}</p>}
        <div className="nfd-actions"><ActionButton ref={firstButton} disabled={!url} className="is-primary" onClick={() => { try { Navigation.NavigateToExternalWeb(url); } catch (e) { setMessage(`打开来源失败：${String(e)}`); } }}>{game.source_id === "steam" ? "打开 Steam 商店" : game.source_id === "flathub" ? "打开 Flathub 安装页" : "打开项目获取页"}</ActionButton>
          <ActionButton disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "稍后再玩"}</ActionButton>
          <ActionButton onClick={onClose}>返回游戏目录</ActionButton></div>
      </div>
    </div>
  </Focusable>;
}
