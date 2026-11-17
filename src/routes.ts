/* ═══════════════════════════════════════════
   NewFreedeck — 路由常量
   ═══════════════════════════════════════════ */

export const HOME_ROUTE = "/newfreedeck/home";
export const LOGIN_ROUTE = "/newfreedeck/login";
export const LIBRARY_ROUTE = "/newfreedeck/library";
export const SETTINGS_ROUTE = "/newfreedeck/settings";
export const FAQ_ROUTE = "/newfreedeck/faq";
export const SHOWCASE_ROUTE = "/newfreedeck/showcase";
export const EMU_MANAGER_ROUTE = "/newfreedeck/emulators";

export const MY_LIBRARY_ROUTE = "/newfreedeck/my-library";
export const EMULATOR_PLATFORMS = ["ps1", "ps2", "ps3", "psp", "switch", "3ds", "gba", "nds", "fc", "md"] as const;
export const platformLibraryRoute = (platform: string) => `${LIBRARY_ROUTE}/${platform}`;
