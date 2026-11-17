import { startCatalogUpdates } from "./catalogUpdates";
import { PageShell } from "./ui";
import LibraryDashboard from "./LibraryDashboard";
import { MY_LIBRARY_ROUTE, EMULATOR_PLATFORMS, platformLibraryRoute } from "./routes";
import { FaGamepad } from "react-icons/fa";
import { getDownloadPoller } from "./downloadPoller";
// Routes, download lifecycle and the optional Steam library entry.

import { definePlugin, routerHook } from "@decky/api";
import HomePanel from "./HomePanel";
import QrLoginPage from "./QrLoginPage";
import LibraryPage from "./LibraryPage";
import SettingsPage from "./SettingsPage";
import EmuManagerPage from "./EmuManagerPage";
import DeckyUiShowcase from "./DeckyUiShowcase";
import { HOME_ROUTE, LOGIN_ROUTE, LIBRARY_ROUTE, SETTINGS_ROUTE, EMU_MANAGER_ROUTE, SHOWCASE_ROUTE } from "./routes";
import { startLibraryTabManager, stopLibraryTabManager } from "./libraryTabs";

/* ── 插件入口 ──────────────────────────── */

export default definePlugin(() => {
  const registrations: Array<{ unregister: () => void }> = [];
  // Steam 库标签注入：让 NewFreedeck 安装的游戏出现在 "已安装" 而非 "非 Steam"
  try {
    startLibraryTabManager();
    registrations.push({ unregister: () => stopLibraryTabManager() });
  } catch (e: any) {
    console.log("[NewFreedeck] library tab manager start failed:", e?.message);
  }

  registrations.push({ unregister: startCatalogUpdates() });

  // 路由注册
  routerHook.addRoute(MY_LIBRARY_ROUTE, () => <PageShell><div className="nfd-content"><LibraryDashboard /></div></PageShell>);
  routerHook.addRoute(HOME_ROUTE, () => <PageShell><div className="nfd-content"><div style={{ maxWidth: 600, margin: "0 auto" }}><HomePanel /></div></div></PageShell>);
  routerHook.addRoute(LOGIN_ROUTE, QrLoginPage);
  routerHook.addRoute(LIBRARY_ROUTE, LibraryPage);
  for (const platform of EMULATOR_PLATFORMS) routerHook.addRoute(platformLibraryRoute(platform), () => <LibraryPage initialPlatform={platform} />);
  routerHook.addRoute(SETTINGS_ROUTE, SettingsPage);
  routerHook.addRoute(EMU_MANAGER_ROUTE, EmuManagerPage);
  routerHook.addRoute(SHOWCASE_ROUTE, DeckyUiShowcase);

  return {
    name: "DeckHarbor",
    title: "DeckHarbor",
    content: <HomePanel />,
    icon: <FaGamepad />,
    onDismount() {
      getDownloadPoller().destroy();
      registrations.forEach((r) => r.unregister());
      routerHook.removeRoute(MY_LIBRARY_ROUTE);
      routerHook.removeRoute(HOME_ROUTE);
      routerHook.removeRoute(LOGIN_ROUTE);
      routerHook.removeRoute(LIBRARY_ROUTE);
      for (const platform of EMULATOR_PLATFORMS) routerHook.removeRoute(platformLibraryRoute(platform));
      routerHook.removeRoute(SETTINGS_ROUTE);
      routerHook.removeRoute(EMU_MANAGER_ROUTE);
      routerHook.removeRoute(SHOWCASE_ROUTE);
    },
  };
});
