import { DialogButton, Dropdown, GamepadButton, Focusable, Toggle, Navigation } from "@decky/ui";
import type { DialogButtonProps, DropdownProps } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

// Keep Steam's focus/input controls, own the surrounding layout.
export function ActionButton({ children, className = "", layout: _layout, ...props }: DialogButtonProps & { layout?: string }) {
  return <DialogButton {...props} className={`nfd-button ${className}`}>{children}</DialogButton>;
}
export function SelectField({ label, ...props }: DropdownProps & { label: ReactNode }) {
  return <div className="nfd-select"><div className="nfd-label">{label}</div><Dropdown {...props} menuLabel={typeof label === "string" ? label : undefined} /></div>;
}
export function SettingToggle({ label, description, checked, onChange, disabled }: {
  label: ReactNode; description?: ReactNode; checked: boolean; onChange?: (value: boolean) => void; disabled?: boolean;
}) {
  return <div className="nfd-toggle-row"><div className="nfd-toggle-copy"><div className="nfd-label">{label}</div>{description && <div className="nfd-muted">{description}</div>}</div><Toggle value={checked} onChange={onChange} disabled={disabled} /></div>;
}
export function Section({ title, children }: { title?: ReactNode; children?: ReactNode }) {
  return <section className="nfd-section">{title && <h3>{title}</h3>}<div className="nfd-section-body">{children}</div></section>;
}
export function Row({ children }: { children?: ReactNode }) { return <div className="nfd-row">{children}</div>; }
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return <header className="nfd-page-header"><div className="nfd-heading"><h1>{title}</h1>{subtitle && <div className="nfd-muted">{subtitle}</div>}</div>{actions && <div className="nfd-header-actions">{actions}</div>}</header>;
}
export function PageShell({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <Focusable className={`nfd-ui nfd-page ${className}`} flow-children="column" onCancel={event => {
    event.preventDefault(); event.stopPropagation(); Navigation.NavigateBack();
  }} onCancelActionDescription="返回"><UIStyles />{children}</Focusable>;
}
export function UIStyles() { return <style>{UI_CSS}</style>; }
export function PageTabs({ tabs, activeTab, onShowTab }: { tabs: { id: string; title: string; content: ReactNode }[]; activeTab: string; onShowTab: (id: string) => void }) {
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = 0; }, [activeTab]);
  return <Focusable className="nfd-tabs" flow-children="column" onButtonDown={event => {
      const button = Number(event?.detail?.button);
      if (button !== GamepadButton.BUMPER_LEFT && button !== GamepadButton.BUMPER_RIGHT) return;
      const current = tabs.findIndex(t => t.id === activeTab);
      const next = Math.max(0, Math.min(tabs.length - 1, current + (button === GamepadButton.BUMPER_LEFT ? -1 : 1)));
      if (!tabs.length) return;
      onShowTab(tabs[next].id); event.preventDefault(); event.stopPropagation();
    }} actionDescriptionMap={{ [GamepadButton.BUMPER_LEFT]: "上一页", [GamepadButton.BUMPER_RIGHT]: "下一页" }}>
    <Focusable className="nfd-tab-bar" flow-children="row">
      {tabs.map(tab => <ActionButton key={tab.id} className={tab.id === activeTab ? "is-active" : ""} onClick={() => onShowTab(tab.id)}>{tab.title}</ActionButton>)}
    </Focusable>
    <div ref={scroll} className="nfd-tab-content" role="region" aria-label={tabs.find(t => t.id === activeTab)?.title}>{tabs.find(t => t.id === activeTab)?.content}</div>
  </Focusable>;
}

// Measure the actual content width so directional navigation matches the rendered grid.
export function useCardColumns(cardWidth = 133, gap = 24, maximum = 7) {
  const [element, gridRef] = useState<HTMLDivElement | null>(null);
  const [columns, setColumns] = useState(1);
  useEffect(() => {
    if (!element) return;
    const update = (width: number) => setColumns(Math.max(1, Math.min(maximum, Math.floor((width + gap) / (cardWidth + gap)))));
    const css = element.ownerDocument.defaultView!.getComputedStyle(element);
    update(element.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight));
    const observer = new ResizeObserver(entries => update(entries[0].contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, [element, cardWidth, gap, maximum]);
  return { gridRef, columns };
}

export const UI_CSS = `
.nfd-ui{--nfd-bg:#101923;--nfd-panel:#192633;--nfd-line:#314353;--nfd-muted:#a7b8c8;color:#edf3f8;font-size:14px;line-height:1.5;font-family:inherit;min-width:0;box-sizing:border-box}
.nfd-ui *, .nfd-ui *::before,.nfd-ui *::after{box-sizing:border-box}
.nfd-page{width:100%;height:100vh;padding:48px 28px 28px;display:flex;flex-direction:column;min-height:0;overflow:hidden;background:var(--nfd-bg)}
.nfd-ui h1,.nfd-ui h2,.nfd-ui h3,.nfd-ui p{margin:0}
.nfd-ui h1{font-size:25px;line-height:1.25;font-weight:700;letter-spacing:.01em}
.nfd-ui h2{font-size:21px;line-height:1.35;font-weight:650}
.nfd-ui h3{font-size:16px;line-height:1.4;font-weight:600}
.nfd-page-header{display:flex;align-items:center;gap:20px;flex:0 0 auto;padding-bottom:18px;min-width:0}
.nfd-heading{flex:1 1 200px;min-width:0}.nfd-heading .nfd-muted{margin-top:6px}
.nfd-muted{color:var(--nfd-muted);font-size:12px;line-height:1.6;overflow-wrap:anywhere}
.nfd-header-actions{display:flex;align-items:center;gap:8px;flex:0 0 auto}
.nfd-ui .nfd-button{box-sizing:border-box!important;display:flex;align-items:center;justify-content:center;min-width:0!important;width:auto!important;max-width:100%;min-height:40px;height:auto!important;margin:0!important;padding:10px 14px!important;background:#25394c;border:1px solid #3b5267;border-radius:6px;box-shadow:none;font-family:inherit;font-size:13px!important;font-weight:500;line-height:1.4!important;white-space:normal!important;overflow-wrap:anywhere;text-align:center;color:#edf3f8;transition:background .12s}
.nfd-ui .nfd-button:hover{background:#334e65}.nfd-ui .nfd-button:disabled,.nfd-ui .nfd-button.Disabled{opacity:.42}

.nfd-ui .nfd-button.is-active{background:#2a5b7d;border-color:#75b6df;color:#fff}
.nfd-ui .nfd-button.is-primary{background:#28688e;border-color:#6bb8e3}
.nfd-ui .nfd-button.is-danger{color:#ffc5bc;background:#442b30;border-color:#795158}
.nfd-ui .nfd-button:focus,.nfd-ui .nfd-button.gpfocus,.nfd-ui .nfd-button.Focusable:focus{background:#d8efff;color:#0c2334;border-color:#a8deff;outline:2px solid #83cfff;outline-offset:2px}
.nfd-ui .nfd-select{display:flex;flex-direction:column;gap:6px;min-width:0;width:100%}
.nfd-ui .nfd-label{font-size:13px;font-weight:600;color:#dce7f1;line-height:1.5}
.nfd-ui .nfd-select> :last-child{min-width:0!important;width:100%!important;max-width:100%;margin:0;box-sizing:border-box}
.nfd-ui .nfd-select [class*="DropDownControl"]{min-width:0!important;width:100%!important;height:auto;min-height:40px;box-sizing:border-box}
.nfd-section{padding:18px;background:var(--nfd-panel);border:1px solid var(--nfd-line);border-radius:8px;min-width:0;margin-bottom:16px}
.nfd-section>h3{margin-bottom:14px}.nfd-section-body{display:flex;flex-direction:column;gap:12px;min-width:0}
.nfd-row{min-width:0;overflow-wrap:anywhere}.nfd-row>.nfd-button{width:100%!important}
.nfd-toggle-row{display:flex;align-items:center;justify-content:space-between;gap:24px;min-width:0;padding:8px 0}
.nfd-toggle-copy{min-width:0;flex:1}.nfd-toggle-copy .nfd-muted{margin-top:4px}.nfd-toggle-row> :last-child{flex-shrink:0}
.nfd-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;min-width:0}.nfd-actions>.nfd-button{width:100%!important}
.nfd-tabs{display:flex;flex-direction:column;flex:1;min-height:0;min-width:0}
.nfd-tab-bar{display:flex;align-items:stretch;gap:8px;flex:0 0 auto;min-width:0;padding:4px 4px 14px;overflow-x:auto;overflow-y:hidden;scrollbar-width:thin}
.nfd-tab-bar>.nfd-button{flex:0 0 auto;white-space:nowrap!important;padding:8px 13px!important;min-height:38px}
.nfd-tab-content{flex:1;min-height:0;min-width:0;overflow-y:auto;overflow-x:hidden;padding:4px 6px 24px;scroll-padding:12px;scrollbar-width:thin}
.nfd-content{min-width:0;min-height:0;flex:1;overflow-y:auto;overflow-x:hidden;padding:4px 6px 24px}
.nfd-dashboard{min-width:0}.nfd-dashboard .nfd-page-header{padding-top:6px}
.nfd-library-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:14px 0 22px}
.nfd-search{max-width:100%;min-width:0}.nfd-search input{width:100%!important;max-width:100%;box-sizing:border-box}
.nfd-library-items{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:16px}
.nfd-library-item{padding:18px;background:var(--nfd-panel);border:1px solid var(--nfd-line);border-radius:8px;min-width:0}
.nfd-game-summary{display:flex;gap:14px;min-width:0;align-items:center;margin-bottom:16px}
.nfd-game-cover{width:64px;height:88px;flex-shrink:0;border-radius:5px;object-fit:cover;background:#293e52;display:grid;place-items:center;font-weight:600;font-size:12px}
.nfd-game-copy{min-width:0;flex:1}.nfd-game-title{font-size:16px;font-weight:650;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;line-height:1.5;margin-bottom:6px}
.nfd-game-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.nfd-game-actions>.nfd-button{width:100%!important}
.nfd-game-actions>.nfd-button:first-child{grid-column:1/-1}
.nfd-message{padding:14px 16px;border-radius:6px;background:#203a4d;overflow-wrap:anywhere;margin:12px 0;white-space:pre-wrap}
.nfd-message.is-error{background:#422b31;color:#ffc7bc}
.nfd-empty{padding:36px 20px;text-align:center;color:var(--nfd-muted);border:1px dashed var(--nfd-line);border-radius:8px}
.nfd-emulator{min-width:0}.nfd-emulator-summary{display:flex;align-items:center;gap:18px;margin:18px 0}.nfd-emulator-summary>img{width:64px;height:64px;object-fit:contain;flex-shrink:0}.nfd-emulator-summary>div{min-width:0;flex:1}
.nfd-emulator-columns{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:18px;align-items:start}.nfd-emulator-columns>div{min-width:0}
.nfd-check{padding:10px 0;border-bottom:1px solid #2e4051;min-width:0}.nfd-check:last-child{border:0}.nfd-path{font-family:monospace;font-size:11px;overflow-wrap:anywhere;word-break:break-word;color:var(--nfd-muted)}
.nfd-settings-body{display:grid;grid-template-columns:168px minmax(0,1fr);gap:24px;flex:1;min-width:0;min-height:0}
.nfd-settings-nav{display:flex;flex-direction:column;gap:8px;overflow-y:auto;padding:4px;min-height:0}.nfd-settings-nav>.nfd-button{justify-content:flex-start;padding-left:18px!important;flex-shrink:0}
.nfd-settings-content{min-height:0;min-width:0;overflow-y:auto;overflow-x:hidden;padding:4px 8px 20px;scroll-padding:16px}
.nfd-settings-section-header{display:flex;justify-content:space-between;align-items:center;gap:16px;padding-bottom:18px;min-width:0}
.nfd-settings-section{min-width:0}.nfd-settings-content .nfd-emulator-columns{grid-template-columns:minmax(0,1fr)}
.nfd-qam{padding:8px 12px 20px;width:100%;min-width:0;background:var(--nfd-bg)}
.nfd-qam .nfd-tab-bar{gap:5px;padding-bottom:12px}.nfd-qam .nfd-tab-bar>.nfd-button{flex:1;padding:8px 4px!important;font-size:12px!important}
.nfd-qam .nfd-tab-content{overflow:visible;padding:4px 0}.nfd-qam .nfd-page-header{display:block;padding-bottom:14px}.nfd-qam h1{font-size:19px}.nfd-qam .nfd-header-actions{display:grid;grid-template-columns:1fr;gap:8px;margin-top:12px}
.nfd-qam .nfd-library-filters{grid-template-columns:1fr;gap:10px}.nfd-qam .nfd-library-items{grid-template-columns:minmax(0,1fr)}.nfd-qam .nfd-library-item{padding:12px}.nfd-qam .nfd-game-actions{grid-template-columns:minmax(0,1fr)}.nfd-qam .nfd-game-cover{width:48px;height:68px}.nfd-qam .nfd-game-title{font-size:14px}.nfd-qam .nfd-section{padding:12px}.nfd-qam .nfd-actions{grid-template-columns:minmax(0,1fr)}
.nfd-catalog-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 4px 12px;flex-shrink:0}
.nfd-external-overlay{position:fixed;inset:0;z-index:10000;background:rgba(6,12,20,.96);padding:48px 36px 36px;overflow-y:auto}
.nfd-external-dialog{max-width:960px;margin:0 auto;display:grid;grid-template-columns:230px minmax(0,1fr);gap:28px;align-items:start}.nfd-external-dialog>div{min-width:0}.nfd-external-cover{width:160px;height:160px;object-fit:contain;margin-bottom:18px}.nfd-external-copy{overflow-wrap:anywhere}.nfd-external-dialog h2{margin-bottom:8px}.nfd-external-copy>.nfd-actions{margin-top:20px}
@media(max-width:580px){.nfd-external-overlay{padding:36px 18px}.nfd-external-dialog{grid-template-columns:minmax(0,1fr)}.nfd-external-cover{width:80px;height:80px}}
@media(max-width:900px){.nfd-page{padding:40px 18px 20px}.nfd-page-header{gap:12px}.nfd-ui h1{font-size:23px}.nfd-emulator-columns{grid-template-columns:minmax(0,1fr)}.nfd-settings-body{grid-template-columns:132px minmax(0,1fr);gap:16px}.nfd-header-actions{gap:6px}.nfd-header-actions>.nfd-button{padding:8px 10px!important}}
@media(max-width:580px){.nfd-page{padding:36px 12px 16px}.nfd-page-header{align-items:stretch;flex-direction:column;gap:12px}.nfd-heading{flex:auto}.nfd-header-actions{flex-wrap:wrap}.nfd-library-filters{grid-template-columns:minmax(0,1fr)}.nfd-settings-body{display:flex;flex-direction:column;gap:12px}.nfd-settings-nav{flex-direction:row;flex-shrink:0;overflow-x:auto}.nfd-settings-content{flex:1}.nfd-actions{grid-template-columns:minmax(0,1fr)}}
`;
