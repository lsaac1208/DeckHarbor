import { Navigation } from "@decky/ui";
import { MY_LIBRARY_ROUTE } from "./routes";
import { ActionButton as ButtonItem, Section as PanelSection, Row as PanelSectionRow, SettingToggle as ToggleField, PageShell, PageHeader } from "./ui";
import { EmulatorWorkspace } from "./EmuManagerPage";
/* ═══════════════════════════════════════════
   NewFreedeck — 设置页
   ═══════════════════════════════════════════ */

import {
  Focusable,
  DialogButton,
} from "@decky/ui";
import { toaster, openFilePicker, FileSelectionType } from "@decky/api";
import { useState, useEffect, useCallback, useRef } from "react";
import type { ReactNode, CSSProperties, ComponentType } from "react";
import { IoMdRefresh } from "react-icons/io";
import {
  getSettings, setSettings as saveSettingsApi,
  listMediaMounts, clearLogin,
  emulatorList, emulatorDownload, emulatorLaunchAppimage,
  uninstallEmulator,
  downloadPoll,
  cloudSaveUpload, cloudSaveDownload, cloudSaveHistory,
  baiduGetAuthUrl, baiduExchangeCode, baiduCheckLogin, baiduClearLogin,
} from "./api";
import type { EmulatorInfo, DownloadTask, LoginState, CloudSaveRecord } from "./api";
import { upsertDownload } from "./downloadStore";
import { getDownloadPoller } from "./downloadPoller";
import type { SettingsData, StorageMount } from "./api";
import { setLoginState, useLoginState } from "./loginState";
import { readLog } from "./api";

/* ── 工具函数 ────────────────────────── */

async function pickFolder(startPath: string): Promise<string> {
  const base = String(startPath || "/home/deck").trim() || "/home/deck";
  const result = await openFilePicker(FileSelectionType.FOLDER, base, false, true);
  return String(result?.realpath || result?.path || "").trim();
}

function formatFree(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function PageView({ title, description, titleItem, children }: {
  title: ReactNode; description?: ReactNode; titleItem?: ReactNode; fullPage?: boolean; children: ReactNode;
}) {
  return <div className="nfd-settings-section">
    <div className="nfd-settings-section-header"><div><h2>{title}</h2>{description && <p className="nfd-muted">{description}</p>}</div>{titleItem}</div>
    {children}
  </div>;
}

function IconButton({ icon: Icon, onOKActionDescription, disabled, onClick }: {
  icon: ComponentType<{ style?: CSSProperties }>;
  onOKActionDescription: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <ButtonItem
      disabled={disabled}
      onOKActionDescription={onOKActionDescription}
      onClick={onClick}
      style={{ height: "28px", width: "28px", minWidth: 0, padding: "10px 6px" }}
    >
      <Icon style={{ marginTop: "-4px", display: "block" }} />
    </ButtonItem>
  );
}

/* ── 默认值 ──────────────────────────── */

const EMPTY_SETTINGS: SettingsData = {
  download_path: "",
  install_path: "",
  auto_delete_after_install: false,
  auto_suspend_on_download: false,
  emulator_crt_enabled: true,
  lsfg_enabled: true,
  show_playtime_widget: true,
  show_freedeck_library_entry: true,
  screen_off_auto_suspend: false,
  cloud_save_auto_upload: true,
  cloud_save_provider: "",
};

/* ── 基础页 ────────────────────────────── */

function BasicSettings({ settings, saving, mounts, checkingMediaMounts,
  onPickDownloadDir, onUseDownloadDirFromMedia,
  onPickInstallDir, onUseInstallDirFromMedia,
  savePatch,
}: {
  settings: SettingsData;
  saving: boolean;
  mounts: StorageMount[];
  checkingMediaMounts: boolean;
  onPickDownloadDir: () => void;
  onUseDownloadDirFromMedia: () => void;
  onPickInstallDir: () => void;
  onUseInstallDirFromMedia: () => void;
  savePatch: (patch: Partial<SettingsData>, toast?: string) => Promise<void>;
}) {
  return (
    <PageView title="基础" fullPage={false}>
      <PanelSection title="下载目录">
        <PanelSectionRow>
          <div style={{ fontSize: 12, lineHeight: 1.5 }}>{settings.download_path || "未设置"}</div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onPickDownloadDir} disabled={saving}>
            选择下载目录
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem onClick={onUseDownloadDirFromMedia} disabled={saving || checkingMediaMounts}>
            {checkingMediaMounts ? "检测中..." : "内存卡"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="安装目录">
        <PanelSectionRow>
          <div style={{ fontSize: 12, lineHeight: 1.5 }}>{settings.install_path || "未设置"}</div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onPickInstallDir} disabled={saving}>
            选择安装目录
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem onClick={onUseInstallDirFromMedia} disabled={saving || checkingMediaMounts}>
            {checkingMediaMounts ? "检测中..." : "内存卡"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="安装行为">
        <PanelSectionRow>
          <ToggleField
            label="安装后自动删除压缩包"
            description="仅在自动安装成功后生效"
            checked={Boolean(settings.auto_delete_after_install)}
            onChange={(v: boolean) => savePatch({ auto_delete_after_install: v })}
            disabled={saving}
          />
        </PanelSectionRow>
      </PanelSection>
    </PageView>
  );
}

/* ── 运行页 ────────────────────────────── */

function RuntimeSettings({ settings, saving, savePatch }: {
  settings: SettingsData;
  saving: boolean;
  savePatch: (patch: Partial<SettingsData>, toast?: string) => Promise<void>;
}) {
  return (
    <PageView title="运行" fullPage={false}>
      <PanelSection title="库主页">
        <PanelSectionRow>
          <ToggleField
            label="显示时长"
            description="在插件库主页显示累计游玩时长，从启用本版本后开始记录"
            checked={Boolean(settings.show_playtime_widget)}
            onChange={v => savePatch({ show_playtime_widget: v })}
            disabled={saving}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="显示 DeckHarbor 游戏库入口"
            description="在 Steam 游戏库标签栏显示 DeckHarbor 入口，重新进入 Steam 游戏库后生效"
            checked={Boolean(settings.show_freedeck_library_entry)}
            onChange={v => savePatch({ show_freedeck_library_entry: v })}
            disabled={saving}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="启动项">
        <PanelSectionRow>
          <ToggleField
            label="复古模拟器 CRT 滤镜"
            description="影响 Switch / PS1 / PS2 / PS3 / PSP / 3DS / GBA / NDS / MD / FC 导入到 Steam 的快捷方式；关闭后新导入不再套用 CRT，旧 wrapper 启动时也会自动跳过滤镜"
            checked={Boolean(settings.emulator_crt_enabled)}
            onChange={(v: boolean) => savePatch({ emulator_crt_enabled: v })}
            disabled={saving}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="自动添加小黄鸭（LSFG）"
            description="新下载/导入的游戏（PC 与模拟器）自动在启动项添加 ~/lsfg %command% 前缀（需已安装 lsfg-vk；未检测到则跳过）"
            checked={Boolean(settings.lsfg_enabled)}
            onChange={(v: boolean) =>
              savePatch({ lsfg_enabled: v },
                v ? "已开启自动添加小黄鸭（LSFG）" : "已关闭自动添加小黄鸭（LSFG）")
            }
            disabled={saving}
          />
        </PanelSectionRow>
      </PanelSection>
    </PageView>
  );
}

/* ── 账号页 ────────────────────────────── */

function AccountSettings({ login, loginLoading, clearingLogin, onClearLogin, saving }: {
  login: LoginState | null;
  loginLoading: boolean;
  clearingLogin: boolean;
  onClearLogin: () => void;
  saving: boolean;
}) {
  const [baiduLogin, setBaiduLogin] = useState<LoginState | null>(null);
  const [baiduLoading, setBaiduLoading] = useState(false);
  const [baiduClearing, setBaiduClearing] = useState(false);

  const checkBaidu = useCallback(async () => {
    setBaiduLoading(true);
    try {
      const r = await baiduCheckLogin();
      setBaiduLogin(r?.data ?? null);
    } catch {} finally {
      setBaiduLoading(false);
    }
  }, []);

  useEffect(() => { checkBaidu(); }, [checkBaidu]);

  const onBaiduAuth = async () => {
    try {
      const r = await baiduGetAuthUrl();
      const url = r?.data?.url;
      if (url) {
        // 用 Steam 内置浏览器打开授权页，回调页面会自动换 token
        (window as any).SteamClient?.Browser?.OpenURL?.(url) || window.open(url, "_blank");
        toaster.toast({ title: "百度网盘", body: '授权完成后请点击"刷新状态"查看结果', duration: 3000 });
      }
    } catch (e: any) {
      toaster.toast({ title: "百度网盘", body: "授权失败: " + (e?.message || "未知错误"), duration: 3000 });
    }
  };

  const onBaiduClear = async () => {
    setBaiduClearing(true);
    try {
      await baiduClearLogin();
      setBaiduLogin(null);
    } catch {} finally {
      setBaiduClearing(false);
    }
  };

  return (
    <PageView title="账号" fullPage={false}>
      <PanelSection title="天翼云盘">
        <PanelSectionRow>
          <div style={{ fontSize: 12, lineHeight: 1.5 }}>
            {loginLoading
              ? "正在检查登录状态..."
              : login?.logged_in
                ? `当前已登录：${login.account_name || "未知账号"}`
                : "当前未登录"}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onClearLogin} disabled={clearingLogin || saving || !login?.logged_in}>
            {clearingLogin ? "注销中..." : "注销天翼云盘账号"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="百度网盘">
        <PanelSectionRow>
          <div style={{ fontSize: 12, lineHeight: 1.5 }}>
            {baiduLoading
              ? "正在检查授权状态..."
              : baiduLogin?.logged_in
                ? `当前已授权：${baiduLogin.account_name || "百度用户"}`
                : "当前未授权"}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ display: "flex", gap: 8 }}>
            <div style={{ flex: 1 }}>
              <ButtonItem layout="below" onClick={onBaiduAuth} disabled={baiduLoading || baiduLogin?.logged_in}>
                {baiduLoading ? "检查中..." : "授权百度网盘"}
              </ButtonItem>
            </div>
            <div style={{ flex: 1 }}>
              <ButtonItem layout="below" onClick={checkBaidu} disabled={baiduLoading}>
                刷新状态
              </ButtonItem>
            </div>
          </div>
        </PanelSectionRow>
        {baiduLogin?.logged_in && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={onBaiduClear} disabled={baiduClearing}>
              {baiduClearing ? "注销中..." : "注销百度网盘授权"}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
    </PageView>
  );
}

/* ═══════════════════════════════════════════
   [测试] 下载列表 UI 测试面板 — 用完即删
   删除时可以整个 TestDownloadPanel 函数连同
   SidebarNavigation 里对应的 page 条目一起删
   ═══════════════════════════════════════════ */

function LogViewer() {
  const [log, setLog] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLog = useCallback(async (silent?: boolean) => {
    if (!silent) setLoading(true); else setRefreshing(true);
    setError("");
    try {
      const r = await readLog({ lines: 300 });
      if (r?.data?.log != null) setLog(r.data.log);
      else setError(r?.message || "无法获取日志");
    } catch {
      setError("读取日志失败");
    }
    setLoading(false); setRefreshing(false);
  }, []);

  useEffect(() => { fetchLog(); }, [fetchLog]);

  // 自动刷新
  useEffect(() => {
    if (autoRefresh) {
      timerRef.current = setInterval(() => fetchLog(true), 5000);
    } else {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [autoRefresh, fetchLog]);

  // 滚动到底部
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [log]);

  return (
    <PageView title="日志" fullPage={false}
      titleItem={
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <ToggleField
            label=""
            checked={autoRefresh}
            onChange={(v: boolean) => setAutoRefresh(v)}
            description={autoRefresh ? "自动刷新 (5s)" : "手动刷新"}
          />
          <IconButton icon={IoMdRefresh} onOKActionDescription="刷新日志"
            disabled={refreshing} onClick={() => fetchLog(true)} />
        </div>
      }>
      <PanelSection>
        <PanelSectionRow>
          <div ref={scrollRef} style={{
            maxHeight: "420px", overflowY: "auto",
            background: "rgba(0,0,0,0.3)", borderRadius: 0, padding: 12,
            fontFamily: "monospace", fontSize: 11, lineHeight: 1.5,
            color: "#8b929a", whiteSpace: "pre-wrap", wordBreak: "break-all",
          }}>
            {loading ? "加载中..." : error ? <span style={{ color: "#e74a2f" }}>{error}</span> : log || "暂无日志"}
          </div>
        </PanelSectionRow>
      </PanelSection>
    </PageView>
  );
}

export default function SettingsPage() {
  const [activePage, setActivePage] = useState("基础");
  const contentRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (contentRef.current) contentRef.current.scrollTop = 0; }, [activePage]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [settings, setSettings] = useState<SettingsData>(EMPTY_SETTINGS);
  const [mounts, setMounts] = useState<StorageMount[]>([]);
  const [checkingMediaMounts, setCheckingMediaMounts] = useState(false);

  // 登录态
  const { login, loading: loginLoading } = useLoginState({ validateOnMount: true });
  const [clearingLogin, setClearingLogin] = useState(false);

  // ── 加载设置 ──
  const loadSettings = useCallback(async () => {
    setLoading(true); setLoadError("");
    try {
      const r = await getSettings();
      if (r?.status !== "success" || !r.data) throw new Error(r?.message || "设置加载失败");
      setSettings({ ...EMPTY_SETTINGS, ...r.data } as SettingsData);
    } catch (error) {
      setLoadError(String((error as Error)?.message || error));
    } finally { setLoading(false); }
  }, []);

  // ── 加载挂载点 ──
  const loadMounts = useCallback(async () => {
    setCheckingMediaMounts(true);
    try {
      const r = await listMediaMounts();
      setMounts(r?.data?.mounts ?? []);
    } catch {} finally {
      setCheckingMediaMounts(false);
    }
  }, []);

  useEffect(() => { loadSettings(); loadMounts(); }, []);

  // ── 保存 ──
  const savePatch = useCallback(async (patch: Partial<SettingsData>, toast?: string) => {
    setSaving(true);
    try {
      const r = await saveSettingsApi(patch);
      if (r?.status !== "success" || !r.data) throw new Error(r?.message || "设置保存失败");
      if (r?.data) setSettings({ ...EMPTY_SETTINGS, ...r.data } as SettingsData);
      window.dispatchEvent(new CustomEvent("newfreedeck-settings-changed"));
      if (toast) toaster.toast({ title: "设置已保存", body: toast, duration: 2000 });
    } catch (e) {
      // 不再静默吞错：失败必须让用户看到
      console.error("[NewFreedeck] saveSettings failed:", e);
      toaster.toast({ title: "设置保存失败", body: String((e as Error)?.message || e || "未知错误"), duration: 3000 });
    } finally {
      setSaving(false);
    }
  }, []);

  // ── 内存卡挂载 ──
  const fetchMediaMounts = useCallback(async (): Promise<StorageMount[] | null> => {
    if (checkingMediaMounts) return null;
    setCheckingMediaMounts(true);
    try {
      const r = await listMediaMounts();
      const mounts: StorageMount[] = Array.isArray(r?.data?.mounts) ? r.data.mounts : [];
      return mounts
        .map(m => ({ ...m, path: String(m.path || "").trim() }))
        .filter(m => Boolean(m.path));
    } finally {
      setCheckingMediaMounts(false);
    }
  }, [checkingMediaMounts]);

  const onPickDownloadDir = useCallback(async () => {
    try {
      const selected = await pickFolder(settings.download_path || "/home/deck");
      if (!selected) return;
      await savePatch({ download_path: selected }, `下载目录已更新：${selected}`);
    } catch (error) {
      toaster.toast({ title: "DeckHarbor", body: `选择下载目录失败：${error}` });
    }
  }, [savePatch, settings.download_path]);

  const onPickInstallDir = useCallback(async () => {
    try {
      const selected = await pickFolder(settings.install_path || settings.download_path || "/home/deck");
      if (!selected) return;
      await savePatch({ install_path: selected }, `安装目录已更新：${selected}`);
    } catch (error) {
      toaster.toast({ title: "DeckHarbor", body: `选择安装目录失败：${error}` });
    }
  }, [savePatch, settings.download_path, settings.install_path]);

  const onUseDownloadDirFromMedia = useCallback(async () => {
    try {
      const mounts = await fetchMediaMounts();
      if (!mounts) return;
      if (mounts.length <= 0) {
        toaster.toast({ title: "DeckHarbor", body: "未检测到外接设备（/run/media）" });
        return;
      }
      const m = mounts[0];
      const target = `${String(m.path || "").trim()}/Game`;
      await savePatch({ download_path: target }, `下载目录已更新：${target}`);
    } catch (error) {
      toaster.toast({ title: "DeckHarbor", body: `加载外接设备失败：${error}` });
    }
  }, [fetchMediaMounts, savePatch]);

  const onUseInstallDirFromMedia = useCallback(async () => {
    try {
      const mounts = await fetchMediaMounts();
      if (!mounts) return;
      if (mounts.length <= 0) {
        toaster.toast({ title: "DeckHarbor", body: "未检测到外接设备（/run/media）" });
        return;
      }
      const m = mounts[0];
      const target = `${String(m.path || "").trim()}/Game`;
      await savePatch({ install_path: target }, `安装目录已更新：${target}`);
    } catch (error) {
      toaster.toast({ title: "DeckHarbor", body: `加载外接设备失败：${error}` });
    }
  }, [fetchMediaMounts, savePatch]);

  // ── 清除登录态 ──
  const onClearLogin = useCallback(async () => {
    setClearingLogin(true);
    try {
      const r = await clearLogin();
      if (r?.data) setLoginState(r.data);
    } catch {} finally {
      setClearingLogin(false);
    }
  }, []);

  // ── 模拟器刷新 key ──
  const [emuRefreshKey, setEmuRefreshKey] = useState(0);

  if (loading || loadError) {
    return <PageShell><PageHeader title="设置" /><div className="nfd-content">
      <div className={loadError ? "nfd-message is-error" : "nfd-message"}>{loadError || "加载设置…"}</div>
      {loadError && <ButtonItem onClick={() => void loadSettings()}>重新加载</ButtonItem>}
    </div></PageShell>;
  }

  const s = settings;

  const pages = [
    { title: "基础", content: <BasicSettings settings={s} saving={saving} mounts={mounts} checkingMediaMounts={checkingMediaMounts}
        onPickDownloadDir={onPickDownloadDir} onUseDownloadDirFromMedia={onUseDownloadDirFromMedia}
        onPickInstallDir={onPickInstallDir} onUseInstallDirFromMedia={onUseInstallDirFromMedia} savePatch={savePatch} /> },
    { title: "运行", content: <RuntimeSettings settings={s} saving={saving} savePatch={savePatch} /> },
    { title: "模拟器", content: <EmulatorWorkspace /> },
    { title: "账号", content: <AccountSettings login={login} loginLoading={loginLoading} clearingLogin={clearingLogin} onClearLogin={onClearLogin} saving={saving} /> },
    { title: "云存档", content: <CloudSaveSettings saving={saving} savePatch={savePatch} /> },
    { title: "日志", content: <LogViewer /> },
  ];
  return <PageShell>
    <PageHeader title="设置" subtitle="存储、运行偏好与账号管理" actions={<ButtonItem onClick={() => Navigation.Navigate(MY_LIBRARY_ROUTE)}>我的游戏</ButtonItem>} />
    <div className="nfd-settings-body">
      <Focusable flow-children="column" role="navigation" className="nfd-settings-nav" aria-label="设置分类">{pages.map(page => <ButtonItem key={page.title} className={activePage === page.title ? "is-active" : ""} onClick={() => setActivePage(page.title)}>{page.title}</ButtonItem>)}</Focusable>
      <div ref={contentRef} className="nfd-settings-content" role="region" aria-label={activePage}>{pages.find(page => page.title === activePage)?.content}</div>
    </div>
  </PageShell>;
}

/* ── 云存档组件 ─────────────────────── */

function formatDate(ts: string): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts.slice(0, 16);
    return d.toLocaleString();
  } catch {
    return ts.slice(0, 16);
  }
}

function CloudSaveSettings({ saving, savePatch }: {
  saving: boolean;
  savePatch: (patch: Partial<SettingsData>, toast?: string) => Promise<void>;
}) {
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [history, setHistory] = useState<CloudSaveRecord[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [lastMessage, setLastMessage] = useState("");
  const [autoSync, setAutoSync] = useState(false);

  // 初始化 autoSync 状态（从 syncTaskQueue 读取持久值）
  useEffect(() => {
    import("./syncQueue").then(({ syncTaskQueue }) => {
      setAutoSync(syncTaskQueue.autoSyncEnabled);
    });
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const r = await cloudSaveHistory();
      setHistory(r?.data?.records ?? []);
    } catch {} finally {
      setHistoryLoaded(true);
    }
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const onUpload = async () => {
    setUploading(true);
    setLastMessage("");
    try {
      const r = await cloudSaveUpload();
      if (r?.data) {
        const msg = r.data.message || `上传完成，共 ${r.data.count} 个存档包`;
        toaster.toast({ title: "云存档", body: msg, duration: 3000 });
        setLastMessage(msg);
        await loadHistory();
      } else {
        setLastMessage(r?.message || "上传失败");
        toaster.toast({ title: "云存档", body: r?.message || "上传失败", duration: 3000 });
      }
    } catch (e: any) {
      setLastMessage("上传失败: " + (e?.message || "网络错误"));
      toaster.toast({ title: "云存档", body: "上传失败", duration: 3000 });
    } finally { setUploading(false); }
  };

  const onDownload = async () => {
    setDownloading(true);
    setLastMessage("");
    try {
      const r = await cloudSaveDownload();
      if (r?.data) {
        const msg = r.data.message || `下载完成，共 ${r.data.count} 个存档包`;
        toaster.toast({ title: "云存档", body: msg, duration: 3000 });
        setLastMessage(msg);
        await loadHistory();
      } else {
        setLastMessage(r?.message || "下载失败");
        toaster.toast({ title: "云存档", body: r?.message || "下载失败", duration: 3000 });
      }
    } catch (e: any) {
      setLastMessage("下载失败: " + (e?.message || "网络错误"));
      toaster.toast({ title: "云存档", body: "下载失败", duration: 3000 });
    } finally { setDownloading(false); }
  };

  const onAutoSyncToggle = (v: boolean) => {
    setAutoSync(v);
    // 通知 syncTaskQueue 更新自动同步状态
    import("./syncQueue").then(({ syncTaskQueue }) => {
      syncTaskQueue.setAutoSyncEnabled(v);
    });
    toaster.toast({
      title: "云存档",
      body: v ? "已开启自动同步" : "已关闭自动同步",
      duration: 2000,
    });
  };

  return (
    <PageView title="云存档" fullPage={false}>
      <PanelSection title="自动同步">
        <PanelSectionRow>
          <ToggleField
            label="游戏启动/退出时自动同步存档"
            description="启用后，每次启动游戏时自动下载云端存档，退出时自动上传本地存档"
            checked={autoSync}
            onChange={onAutoSyncToggle}
            disabled={false}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="操作">
        <PanelSectionRow>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, width: "100%" }}>
            <ButtonItem onClick={onUpload} disabled={uploading || downloading || saving}>
              {uploading ? "上传中..." : "上传云存档（备份）"}
            </ButtonItem>
            <ButtonItem onClick={onDownload} disabled={uploading || downloading || saving}>
              {downloading ? "下载中..." : "下载云存档（恢复）"}
            </ButtonItem>
          </div>
        </PanelSectionRow>
        {lastMessage && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#8b929a", lineHeight: 1.5 }}>{lastMessage}</div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="同步记录">
        {!historyLoaded ? (
          <PanelSectionRow>
            <div style={{ color: "#8b929a", fontSize: 12 }}>加载中...</div>
          </PanelSectionRow>
        ) : history.length === 0 ? (
          <PanelSectionRow>
            <div style={{ color: "#8b929a", fontSize: 12 }}>暂无同步记录</div>
          </PanelSectionRow>
        ) : (
          history.map(record => (
            <PanelSectionRow key={record.id}>
              <div style={{ display: "flex", justifyContent: "space-between", width: "100%", alignItems: "center" }}>
                <span style={{ fontSize: 12, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {record.file_name}
                </span>
                <span style={{
                  fontSize: 11,
                  color: record.direction === "upload" ? "#6fb7ff" : "#5a5",
                  margin: "0 12px",
                  whiteSpace: "nowrap",
                }}>
                  {record.direction === "upload" ? "↑ 上传" : "↓ 下载"}
                </span>
                <span style={{ fontSize: 11, color: "#8b929a", whiteSpace: "nowrap" }}>
                  {formatDate(record.timestamp)}
                </span>
              </div>
            </PanelSectionRow>
          ))
        )}
      </PanelSection>
    </PageView>
  );
}
