import { ActionButton as ButtonItem, SelectField as DropdownItem, PageShell, PageHeader, Section, UIStyles } from "./ui";
import { Focusable, SteamSpinner, Navigation, showModal, ConfirmModal } from "@decky/ui";
import { openFilePicker, FileSelectionType, toaster } from "@decky/api";
import { useCallback, useEffect, useRef, useState } from "react";
import { emulatorImportFirmware, emulatorInventory, emulatorRegisterLocal, emulatorConfigure, emulatorRestoreConfig, emulatorRemove, emulatorPrepareShortcut, emulatorDownload, libraryPrepareRom } from "./api";
import type { EmulatorDetail, DownloadTask } from "./api";
import { announceLibraryChange, ensureShortcut, requireData, runShortcut } from "./steamLaunch";
import { getDownloadQueue, onDownloadChange, upsertDownload } from "./downloadStore";
import { getDownloadPoller } from "./downloadPoller";
import { downloadFailed, downloadSucceeded } from "./downloadState";
import { platformLibraryRoute, MY_LIBRARY_ROUTE } from "./routes";

export function EmulatorWorkspace() {
  const [emulators, setEmulators] = useState<EmulatorDetail[]>([]);
  const [platform, setPlatform] = useState("ps1");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [queue, setQueue] = useState(getDownloadQueue);
  const working = useRef(false);
  const alive = useRef(true);
  const loadingRef = useRef(false);
  const load = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try { const data = requireData(await emulatorInventory()); if (alive.current) { setEmulators(data.emulators); setError(""); } }
    catch (e: any) { if (alive.current) setError(e?.message || "模拟器列表加载失败"); }
    finally { loadingRef.current = false; if (alive.current) setLoading(false); }
  }, []);
  useEffect(() => {
    alive.current = true; void load(); void getDownloadPoller().resumeAll();
    const off = onDownloadChange(setQueue);
    const timer = setInterval(() => void load(), 15000);
    return () => { alive.current = false; off(); clearInterval(timer); };
  }, [load]);
  const emu = emulators.find(e => e.platform === platform);
  const task = queue.find(t => t.game_id === `${platform}_emulator`);
  const installing = !!task && !downloadFailed(task) && !downloadSucceeded(task);
  const taskText = (t: DownloadTask) => downloadFailed(t) ? (t.import_error || t.extract_error || t.error || "安装失败")
    : downloadSucceeded(t) ? "安装完成" : t.extract_status === "running" ? `解压 ${Math.round(t.extract_progress || 0)}%`
    : t.import_status ? "部署模拟器与配置中…" : `${t.status === "paused" ? "已暂停" : "下载"} ${Math.round(t.progress || 0)}%`;
  const act = async (operation: () => Promise<string | void>) => {
    if (working.current) return;
    working.current = true; setBusy(true); setMessage("");
    try { const msg = await operation(); if (alive.current) setMessage(msg || "操作完成"); await load(); }
    catch (e: any) { if (alive.current) setMessage(e?.message || "操作失败"); }
    finally { working.current = false; if (alive.current) setBusy(false); }
  };
  const pick = async () => {
    const result = await openFilePicker(FileSelectionType.FILE, "/home/deck", true, true);
    return String(result?.realpath || result?.path || "").trim();
  };
  const importRom = () => act(async () => {
    const path = await pick(); if (!path) return "已取消选择";
    const prepared = requireData(await libraryPrepareRom({ platform, path }));
    await ensureShortcut(prepared); announceLibraryChange();
    return `「${prepared.title}」已导入游戏库，原文件保留在原位置`;
  });
  const open = () => act(async () => {
    const prepared = requireData(await emulatorPrepareShortcut({ platform }));
    await runShortcut(await ensureShortcut(prepared));
    return "已交给 Steam 启动";
  });
  const confirm = (title: string, description: string, operation: () => Promise<string | void>) => {
    const modal = showModal(<ConfirmModal strTitle={title} strDescription={description} strOKButtonText="继续" strCancelButtonText="取消"
      onCancel={() => modal.Close()} onOK={() => { modal.Close(); void act(operation); }} />);
  };
  return <div className="nfd-ui nfd-emulator"><UIStyles />
    <PageHeader title="模拟器中心" subtitle="安装、配置与本地 ROM 导入" actions={<><ButtonItem onClick={() => Navigation.Navigate(MY_LIBRARY_ROUTE)}>我的游戏</ButtonItem><ButtonItem disabled={busy} onClick={() => void load()}>刷新</ButtonItem></>} />
    {error && <div role="alert">{error}<ButtonItem onClick={() => void load()}>重试</ButtonItem></div>}
    {loading ? <SteamSpinner style={{ width: 36, height: 36 }} /> : <>
      <DropdownItem label="平台" selectedOption={platform} rgOptions={emulators.map(e => ({ data: e.platform, label: `${e.name} · ${e.adapter}${e.installed ? " · 已就绪" : ""}` }))} onChange={o => { setPlatform(String(o.data)); setMessage(""); }} disabled={busy} />
      {emu && <>
        <div className="nfd-emulator-summary">
          <img src={`http://127.0.0.1:20065/emulator_icons/${emu.icon}`} alt="" style={{ width: 72, height: 64, objectFit: "contain" }} />
          <div><strong style={{ fontSize: 20 }}>{emu.name} / {emu.adapter}</strong><div>{emu.installed ? (emu.external ? "已关联本地模拟器" : "已安装") : "尚未安装或文件已移动"}</div><div style={{ fontSize: 12, color: "#9fb2c5", overflowWrap: "anywhere" }}>{emu.executable || emu.install_dir}</div></div>
        </div>
        <div className="nfd-emulator-columns"><div><Section title="管理与启动"><div className="nfd-actions">
          <ButtonItem disabled={busy || installing || emu.installed} onClick={() => void act(async () => {
            const result = requireData(await emulatorDownload({ platform }));
            upsertDownload(result as unknown as DownloadTask); getDownloadPoller().addTask(result.task_id);
            return "下载已加入任务列表，可返回主页查看进度";
          })}>{installing ? "正在安装" : "下载安装"}</ButtonItem>
          <ButtonItem disabled={busy || installing} onClick={() => void act(async () => {
            const path = await pick(); if (!path) return "已取消选择";
            requireData(await emulatorRegisterLocal({ platform, path })); return "本地模拟器已关联，不移动程序文件";
          })}>关联本地 AppImage / 程序</ButtonItem>
          <ButtonItem disabled={busy || !emu.installed || installing} onClick={() => void open()}>启动模拟器</ButtonItem>
          <ButtonItem disabled={busy || !emu.installed || installing} onClick={() => void importRom()}>导入本地 ROM</ButtonItem>
          <ButtonItem disabled={busy || !emu.installed || installing} onClick={() => confirm("应用推荐配置", "会备份当前配置，再写入本平台的便携、语言与输入预设。请先关闭模拟器。", async () => {
            const result = requireData(await emulatorConfigure({ platform }));
            if (!result.ok) throw new Error(result.messages.join("；"));
            return result.messages.join("；") + ` · 备份 ${result.backup}`;
          })}>备份并应用推荐配置</ButtonItem>
          <ButtonItem disabled={busy || !emu.installed || !emu.latest_backup || installing} onClick={() => confirm("恢复最近配置", `恢复 ${emu.latest_backup}；当前配置会先另存备份。`, async () => {
            const result = requireData(await emulatorRestoreConfig({ platform, backup: emu.latest_backup })); return result.messages.join("；");
          })}>恢复最近配置</ButtonItem>
          <ButtonItem disabled={busy || installing || !emu.installed} onClick={() => confirm(emu.external ? "解除本地关联" : "移除模拟器", emu.external ? "原模拟器、配置、ROM 和存档保留。" : "模拟器目录会移入备份，配置与存档保留。使用此模拟器的游戏需重新安装或关联后才能运行。", async () => {
            const result = requireData(await emulatorRemove({ platform }));
            for (const id of result.appids) {
              try {
                const apps = (window as any).SteamClient?.Apps;
                if (!apps?.RemoveShortcut) throw new Error("Steam API unavailable");
                await apps.RemoveShortcut(id);
              }
              catch { toaster.toast({ title: "模拟器已移除", body: "Steam 中的模拟器快捷方式请手动移除" }); }
            }
            announceLibraryChange(); return result.message + (result.backup ? `：${result.backup}` : "");
          })}>{emu.external ? "解除关联" : "移除并保留存档"}</ButtonItem>
          <ButtonItem onClick={() => Navigation.Navigate(platformLibraryRoute(platform))}>浏览平台游戏</ButtonItem>
        </div>
        </Section>
        {task && <div role="status" style={{ padding: 12, background: "#17283a", marginTop: 12 }}>{taskText(task)}</div>}
        {message && <div role="status" style={{ padding: 12, background: "#243143", marginTop: 12, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{message}</div>}
        </div><div><Section title="安装检查">
        {emu.checks.map(check => <div key={check.label + check.path} className="nfd-check">
          <span style={{ color: check.ok ? "#8acb99" : "#e9ba79" }}>{check.ok ? "✓" : "○"} {check.label}：{check.ok ? "已找到" : "尚未就绪"}</span>
          <div style={{ color: "#9fb2c5", fontSize: 12, overflowWrap: "anywhere" }}>{check.path}</div>
        </div>)}
        </Section><Section title="BIOS / 固件">
        {["ps1", "ps2", "nds", "switch"].includes(platform) && <ButtonItem disabled={busy || installing || !emu.installed} onClick={() => void act(async () => {
          const path = await pick(); if (!path) return "已取消选择";
          return requireData(await emulatorImportFirmware({ platform, path })).message;
        })}>导入自己的 BIOS / 密钥文件</ButtonItem>}
        <p style={{ lineHeight: 1.7 }}>{emu.guidance}</p>
        <p style={{ color: "#9fb2c5", fontSize: 12 }}>检测到 {emu.firmware_files.length} 个相关文件。此处仅检查文件是否存在，实际有效性由模拟器验证。配置备份 {emu.backup_count} 份。</p>
        {emu.firmware_files.map(path => <div key={path} className="nfd-path">{path}</div>)}
        </Section></div></div>
      </>}
    </>}
  </div>;
}

export default function EmuManagerPage() {
  return <PageShell><div className="nfd-content"><EmulatorWorkspace /></div></PageShell>;
}
