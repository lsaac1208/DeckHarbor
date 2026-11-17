/* ═══════════════════════════════════════════
   NewFreedeck — CSS
   ═══════════════════════════════════════════ */

export const LIBRARY_CSS = `
.freedeck-library{width:100%;height:100vh;display:flex;flex-direction:column;background:#0e141b;color:#cac4d0}
.freedeck-library-header{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;border-bottom:1px solid rgba(255,255,255,0.06);background:rgba(0,0,0,0.3);flex:0 0 auto}
.freedeck-library-header-title{font-size:18px;font-weight:700;color:#f4f8fc;margin:0;letter-spacing:0.02em}
.freedeck-library-empty{flex:1;display:flex;align-items:center;justify-content:center;padding:60px;color:#8b929a;font-size:13px}

/* ── GameCard (freedeck-plugin 移植) ── */
.freedeck-library-grid-card{position:relative;flex:0 0 auto;width:133px;min-width:133px}
/* 聚焦触发规则已移至全局 FOCUS_TRIGGER_CSS（v0.28：HomePanel 也需生效，故全局注入） */
.freedeck-library-grid-card-button{transition:background 100ms ease}
.freedeck-library-grid-card-button .Panel{border:none!important;background:transparent!important;box-shadow:none!important}
.freedeck-library-grid-card-frame{position:relative;width:100%;height:200px;overflow:visible;padding:0}
.freedeck-library-grid-card-backdrop{display:none}
.freedeck-library-grid-card-focus-state .freedeck-library-grid-card-backdrop{display:none}


.freedeck-library-grid-card-cover-wrap{position:relative;width:100%;height:200px;min-height:200px;max-height:200px;overflow:visible;background:rgba(255,255,255,0.04);display:flex;align-items:center;justify-content:center}
.freedeck-library-grid-card-focus-state .freedeck-library-grid-card-cover-wrap{}
.freedeck-library-grid-card-cover-wrap::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(255,255,255,.015),rgba(0,0,0,.08));opacity:.28;pointer-events:none;z-index:1}
.freedeck-library-grid-card-cover{display:block;width:100%;height:100%;object-fit:cover}
.freedeck-library-grid-card-sideinfo{position:absolute;top:50%;left:calc(100% + 6px);width:176px;display:grid;gap:6px;padding:10px 12px;opacity:0;pointer-events:none;transform:translate3d(-6px,-50%,0);transition:opacity 100ms ease,transform 120ms ease;z-index:2;box-sizing:border-box}
.freedeck-library-grid-card-focus-state .freedeck-library-grid-card-sideinfo{opacity:1;transform:translate3d(0,-50%,0)}
.freedeck-library-grid-card-sideinfo.is-sideinfo-left{left:auto;right:calc(100% + 6px);transform:translate3d(6px,-50%,0)}
.freedeck-library-grid-card-focus-state .freedeck-library-grid-card-sideinfo.is-sideinfo-left{transform:translate3d(0,-50%,0)}
.freedeck-library-grid-card-sideinfo-title{font-size:14px;line-height:1.3;font-weight:700;color:#f4f8fb;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 2px rgba(0,0,0,.8),0 2px 8px rgba(0,0,0,.6)}
.freedeck-library-grid-card-sideinfo-subtitle{font-size:11px;line-height:1.35;font-weight:500;color:rgba(236,242,247,.92);max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 2px rgba(0,0,0,.8)}
.freedeck-library-grid-card-sideinfo-size{font-size:11px;line-height:1.35;font-weight:600;color:rgba(188,223,244,.96);margin-top:2px;text-shadow:0 1px 2px rgba(0,0,0,.8)}

/* Keep captions inside each cover; floating side panels overlap adjacent games. */
.nfd-ui .freedeck-library-grid-card-sideinfo,
.nfd-ui .freedeck-library-grid-card-focus-state .freedeck-library-grid-card-sideinfo,
.nfd-ui .freedeck-library-grid-card-sideinfo.is-sideinfo-left{left:0;right:0;top:auto;bottom:0;width:100%;padding:24px 10px 10px;gap:3px;opacity:1;transform:none;background:linear-gradient(transparent,rgba(8,15,23,.96));border-radius:0 0 6px 6px}
.nfd-ui .freedeck-library-grid-card-sideinfo-title{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;font-size:13px;line-height:1.45}
.nfd-ui .freedeck-library-grid-card-sideinfo-subtitle{display:none}
.nfd-ui .freedeck-library-grid-card-button{border-radius:6px!important}
.nfd-ui .freedeck-library-grid-card-cover-wrap{border-radius:6px;background:#25394c}
.nfd-ui .freedeck-library-grid-card-focus-state{outline:2px solid #83cfff;outline-offset:3px}

/* ── LoadMoreCard ── */
.freedeck-library-load-more-card{flex:0 0 auto}
.freedeck-library-load-more-card-frame{display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.03);border:1px dashed rgba(255,255,255,0.08);border-radius:0}
.freedeck-library-load-more-card-inner{text-align:center}
.freedeck-library-load-more-card-title{font-size:14px;font-weight:600;color:#cac4d0}
.freedeck-library-load-more-card-subtitle{font-size:11px;color:#8b929a;margin-top:4px}
`;
/* ═══════════════════════════════════════════
   FOCUS_TRIGGER_CSS — 全局聚焦触发规则（v0.28 起唯一来源）
   由 index.tsx 在插件启动时注入 document.head，覆盖所有页面
   （HomePanel/LibraryPage/未来页面）。
   视觉只来自 Steam/decky 原生 ItemFocusAnim 动画（admin 强制标准第2条）
   ═══════════════════════════════════════════ */
export const FOCUS_TRIGGER_CSS = `
.freedeck-library-grid-card-focus-state{z-index:9999!important;animation-name:ItemFocusAnim-darkGrey;animation-duration:0.1s;animation-timing-function:ease-in-out;animation-fill-mode:forwards}
`;
