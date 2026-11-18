// Isolated native Steam UI preview. All plugin/backend actions use fixtures.
if(!window.__nfdReviewRequire)webpackChunksteamui.push([[`nfd-review-${Date.now()}`],{},r=>window.__nfdReviewRequire=r]);
window.__nfdPreviewRoot?.unmount(); document.getElementById('nfd-layout-preview')?.remove();
const iframe=document.createElement('iframe'); iframe.id='nfd-layout-preview';iframe.style.cssText='position:absolute;left:0;top:0;width:1280px;height:800px;border:0;z-index:99999';
document.body.append(iframe);const doc=iframe.contentDocument;
doc.open();doc.write('<!doctype html><html><head></head><body style="margin:0"><div id="preview-root"></div></body></html>');doc.close();
for(const sheet of document.styleSheets){if(sheet.href){const link=doc.createElement('link');link.rel='stylesheet';link.href=sheet.href;doc.head.append(link);}}
const reset=doc.createElement('style');reset.textContent='html,body,#preview-root{margin:0;width:100%;height:100%;background:#101923;font-family:Arial,"Noto Sans CJK SC",sans-serif}';doc.head.append(reset);
const ok=data=>Promise.resolve({status:'success',data});
const games=docsPreview?communityGames.filter(g=>g.source_id==='flathub').slice(0,8).map((g,i)=>({...g,game_id:'demo-'+i,platform:'pc',steam_appid:2147483666+i,favorite:i===0,running:false,available:true,last_played:0,playtime_seconds:1200*(i+1),managed_files:false,installed_at:'2026-11-17'})):Array.from({length:8},(_,i)=>({game_id:'g'+i,title:['超级马里奥 奥德赛','最终幻想 X 高清重制版 — 中文版','星之卡比 探索发现','塞尔达传说 旷野之息'][i%4],platform:['switch','ps2','gba','pc'][i%4],steam_appid:2147483666+i,favorite:i===0,running:i===2,available:i!==3,last_played:i?1770000000:0,playtime_seconds:4000*(i+1),managed_files:i%2===0,installed_at:'2026-11-16'}));
const settings={download_path:docsPreview?'/home/deck/FreedeckGames/Downloads':'/run/media/deck/SteamDeck/非常长的下载目录名称/Downloaded Games',install_path:'/home/deck/FreedeckGames',show_playtime_widget:true,show_freedeck_library_entry:true,emulator_crt_enabled:true,lsfg_enabled:false,cloud_save_auto_upload:false};
const emulators=['ps1','ps2','ps3','psp','switch','3ds','gba','nds','fc','md'].map(platform=>({platform,name:platform.toUpperCase(),adapter:{ps1:'DuckStation',ps2:'PCSX2',switch:'Eden'}[platform]||platform,icon:docsPreview?platform+'.webp':'',installed:true,executable:'/home/deck/FreedeckGames/Emulators/'+platform+'/nested-folder/Emulator-x86_64.AppImage',install_dir:'/home/deck/FreedeckGames/Emulators/'+platform,external:false,checks:[{label:'模拟器程序',path:'/home/deck/FreedeckGames/Emulators/'+platform+'/nested-folder/Emulator-x86_64.AppImage',ok:true},{label:'执行权限',path:'/home/deck/FreedeckGames/Emulators/'+platform+'/Emulator.AppImage',ok:true},{label:'配置文件',path:'/home/deck/FreedeckGames/Emulators/'+platform+'/settings.ini',ok:false}],guidance:'BIOS 放在 bios 目录；请在模拟器中选择正确区域的文件。',firmware_files:[],backup_count:2,latest_backup:'20261116-230001'}));
const catalog=docsPreview?communityGames.filter(g=>g.platform==='pc'):Array.from({length:24},(_,i)=>({game_id:'catalog'+i,item_id:'item'+i,title:'预览游戏 '+i+' — 很长的中文标题',title_zh:'预览游戏 '+i+' — 很长的中文标题',title_en:'Long preview title '+i,platform:'pc',size_text:'12.5 GB',cover_url:''}));
window.__nfdPreviewFailSettings=false;
window.__nfdPreviewCalls=[];
window.__nfdPreviewDownloadError="";
const sourceRows=[{id:'legacy',name:'原有目录',count:24,error:'部分目录暂时不可用',description:'本地目录',updated_at:0,refreshing:false},...['steam','flathub','homebrew'].map(id=>({id,name:({steam:'Steam · 官方免费游戏',flathub:'Flathub · Linux 游戏',homebrew:'4TU · Switch 自制游戏'})[id],count:communityGames.filter(g=>g.source_id===id).length,error:'',description:({steam:'Steam 官方商店获取；免费状态与 Deck 兼容性以商店为准。',flathub:'Linux 免费游戏，打开 Flathub 安装页面。',homebrew:'Switch 自制游戏，打开项目页面；NRO 模拟器兼容性未验证。'})[id],updated_at:0,refreshing:false}))];
const api=new Proxy({getCatalogSources:()=>ok({sources:sourceRows,total:56}),updateCatalog:()=>ok({queued:true,total:56}),playLaterAdd:payload=>{window.__nfdPreviewCalls.push(['playLater',payload]);return ok({added:true});},downloadStart:payload=>{window.__nfdPreviewCalls.push(['download',payload]);return new Promise(resolve=>setTimeout(()=>resolve(window.__nfdPreviewDownloadError?{status:'error',message:window.__nfdPreviewDownloadError}:{status:'success',data:{task_id:'fixture-task',status:'downloading'}}),100));},libraryDashboard:()=>ok({games,show_playtime:true}),emulatorInventory:()=>ok({emulators}),getSettings:()=>window.__nfdPreviewFailSettings?Promise.reject(new Error('测试：读取失败')):ok(settings),setSettings:p=>ok(Object.assign(settings,p)),listMediaMounts:()=>ok({mounts:[]}),readLog:()=>ok({log:'[INFO] UI preview — isolated data\n[INFO] All services ready'}),cloudSaveHistory:()=>ok({records:[]}),getPlatformGames:({platform})=>{const rows=platform==='switch'?communityGames.filter(g=>g.platform==='switch'):catalog;return ok({games:rows,total:rows.length,has_more:false});},getShelfGames:()=>ok({games:catalog}),getCuratedShelf:({platform})=>ok({games:(docsPreview?communityGames.filter(g=>g.platform===platform):catalog).slice(0,8)}),listCatalog:(p={})=>{const rows=p.source&&p.source!=='all'?communityGames.filter(g=>g.server===p.source):catalog;return ok({items:rows,total:rows.length,genres:[],sources:['Steam 官方','Flathub','4TU Homebrew']});},playLaterList:()=>ok({items:catalog.slice(0,10)})},{get:(obj,k)=>obj[k]||(()=>ok({}))});
const jsx=(type,props,...children)=>SP_REACT.createElement(type,props,...children);
const react={...SP_REACT,default:SP_REACT};
const fakeDecky={addEventListener:()=>()=>{},removeEventListener:()=>{},toaster:{toast:()=>{}},FileSelectionType:{FILE:0,FOLDER:1},openFilePicker:async()=>({}),callable:()=>async()=>({status:'success',data:{}})};
const cache={};
function load(key){if(cache[key])return cache[key].exports;const module={exports:{}};cache[key]=module;const localRequire=name=>{
 if(name==='react')return react;
 if(name==='@decky/ui')return {...DFL,showContextMenu:(...args)=>{window.__nfdPreviewCalls.push(['menu']);},Navigation:{Navigate:path=>{window.__nfdPreviewNavigation=path;},NavigateBack:()=>{window.__nfdPreviewNavigation='back';},NavigateToExternalWeb:url=>{window.__nfdPreviewNavigation=url;},CloseSideMenus:()=>{}},Router:{CloseSideMenus:()=>{}}};
 if(name==='@decky/api')return fakeDecky;
 if(name.startsWith('react-icons/'))return new Proxy({},{get:()=>()=>jsx('span',null,'↻')});
 const parts=(key.split('/').slice(0,-1).join('/')+'/'+name).split('/');const normalized=[];for(const p of parts){if(p==='..')normalized.pop();else if(p&&p!=='.')normalized.push(p);}const target=normalized.join('/');
 if(target==='api')return api;
 if(target==='loginState')return {useLoginState:()=>({login:{logged_in:false},loading:false}),setLoginState:()=>{}};
 if(target==='downloadPoller')return {getDownloadPoller:()=>({resumeAll:async()=>{},addTask:()=>{}})};
 return load(target);
};new Function('require','module','exports','React',sources[key])(localRequire,module,module.exports,SP_REACT);return module.exports;}
class Boundary extends SP_REACT.Component {constructor(p){super(p);this.state={error:''}}static getDerivedStateFromError(e){return {error:e.stack||String(e)}}render(){return this.state.error?jsx('pre',{'data-preview-error':true,style:{color:'red'}},this.state.error):this.props.children;}}
const client=Object.entries(__nfdReviewRequire.m).filter(([id,f])=>/\.createRoot=/.test(String(f))).map(([id])=>__nfdReviewRequire(id)).find(m=>m.createRoot);
const QueryProvider=__nfdReviewRequire(3715).Ht;
const queryClient=new (__nfdReviewRequire(38215).E)({defaultOptions:{queries:{retry:false}}});
window.__nfdPreviewRoot=client.createRoot(doc.getElementById('preview-root'));
window.__nfdPreviewLoad=load;
window.__nfdPreviewShow=(page='library',width=1280,height=800)=>{
 iframe.style.width=width+'px';iframe.style.height=height+'px';
 const ui=load('ui'); const component=page.startsWith('install-')?jsx(load('library/InstallOverlay').InstallOverlay,{game:{...catalog[0],game_id:'fixture-game',title:'下载按钮测试',file_name:'fixture.zip',down_url:'https://example.invalid/share',size_bytes:1024},sourceRect:{left:20,top:20,width:180,height:260},onClose:()=>{window.__nfdPreviewCalls.push(['close']);},onStartDownload:(task)=>{window.__nfdPreviewCalls.push(['started',task]);}}):page.startsWith('external-')?jsx(load('library/ExternalGameDetails').ExternalGameDetails,{game:communityGames.find(g=>g.source_id===page.slice(9)),onClose:()=>{window.__nfdPreviewNavigation='close';}}):page==='sources'?jsx(ui.PageShell,null,jsx('div',{className:'nfd-content'},jsx(load('CatalogSourcesPanel').default))):page==='switch'?jsx(load('LibraryPage').default,{initialPlatform:'switch'}):page==='settings'?jsx(load('SettingsPage').default):page==='emulator'?jsx(load('EmuManagerPage').default):page==='login'?jsx(load('QrLoginPage').default):page==='home'?jsx(load('HomePanel').default):page==='discover'?jsx(load('LibraryPage').default):jsx(ui.PageShell,null,jsx('div',{className:'nfd-content'},jsx(load('LibraryDashboard').default)));
 window.__nfdPreviewRoot.render(jsx(Boundary,{key:page+width+(window.__nfdPreviewFailSettings?1:0)},jsx(QueryProvider,{client:queryClient},component)));
};
__nfdPreviewShow();
await new Promise(r=>setTimeout(r,1500));
return {text:doc.body.innerText.slice(0,1500),size:[iframe.clientWidth,iframe.clientHeight]};
