import { useState, useEffect } from "react";
import { Navigation, Router } from "@decky/ui";
import { ActionButton, PageTabs, Section, UIStyles } from "./ui";
import { MY_LIBRARY_ROUTE, EMU_MANAGER_ROUTE, LOGIN_ROUTE, LIBRARY_ROUTE, SETTINGS_ROUTE } from "./routes";
import { DownloadStack } from "./library/DownloadStack";
import { onDownloadChange, getDownloadQueue } from "./downloadStore";
import { getDownloadPoller } from "./downloadPoller";
import { useLoginState } from "./loginState";
import LibraryDashboard from "./LibraryDashboard";

function navigate(path: string) {
  Router.CloseSideMenus?.();
  Navigation.Navigate(path);
}

export default function HomePanel() {
  const [tab, setTab] = useState("home");
  const { login, loading } = useLoginState({ validateOnMount: true, refreshOnVisible: true });
  const [queue, setQueue] = useState(getDownloadQueue);
  useEffect(() => onDownloadChange(setQueue), []);
  useEffect(() => { void getDownloadPoller().resumeAll(); }, []);
  return <div className="nfd-ui nfd-qam"><UIStyles />
    <PageTabs activeTab={tab} onShowTab={setTab} tabs={[
      { id: "home", title: "首页", content: <>
        <Section title="DeckHarbor"><p className="nfd-muted">{loading ? "正在检查登录状态…" : login?.logged_in ? `已登录：${login.account_name || "天翼云盘"}` : "未登录 · 本地游戏管理可直接使用"}</p>
          <div className="nfd-actions">
            <ActionButton className="is-primary" onClick={() => navigate(MY_LIBRARY_ROUTE)}>打开我的游戏</ActionButton>
            <ActionButton onClick={() => navigate(EMU_MANAGER_ROUTE)}>模拟器中心</ActionButton>
            <ActionButton onClick={() => navigate(LIBRARY_ROUTE)}>发现游戏</ActionButton>
            <ActionButton onClick={() => navigate(SETTINGS_ROUTE)}>设置</ActionButton>
            {!loading && !login?.logged_in && <ActionButton onClick={() => navigate(LOGIN_ROUTE)}>登录天翼云盘</ActionButton>}
          </div>
        </Section>
        <DownloadStack tasks={queue} />
        {!queue.length && <p className="nfd-muted">没有进行中的下载。</p>}
      </> },
      { id: "library", title: "我的游戏", content: <LibraryDashboard compact /> },
      { id: "about", title: "关于", content: <Section title="DeckHarbor"><p>掌机港 · Steam Deck 游戏库与模拟器管理</p><p className="nfd-muted">社区升级版 · 0.4.4</p><p className="nfd-muted">基于 NewFreedeck 升级开发的 Steam Deck 游戏库与模拟器管理插件。</p><p className="nfd-muted">改进 Decky 原生界面、目录加载、游戏来源与模拟器管理。</p><p className="nfd-muted">管理本地游戏、关联模拟器、导入 ROM，并通过 Steam 启动。</p><ActionButton onClick={() => navigate(SETTINGS_ROUTE)}>打开设置</ActionButton></Section> },
    ]} />
  </div>;
}
