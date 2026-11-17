(async()=>{
 const wait=()=>new Promise(r=>setTimeout(r,650));const checks=[];
 const doc=()=>document.getElementById('nfd-layout-preview').contentDocument;
 for(const source of ['steam','flathub','homebrew']){
  __nfdPreviewShow('external-'+source,1280,800);await wait();window.__nfdPreviewCalls=[];window.__nfdPreviewNavigation='';
  const button=[...doc().querySelectorAll('.nfd-button')].find(b=>b.textContent.includes('打开'));
  button.click();await wait();
  checks.push({name:source+' opens source page',pass:window.__nfdPreviewNavigation.startsWith('https://')});
  checks.push({name:source+' does not start cloud download',pass:!window.__nfdPreviewCalls.some(c=>c[0]==='download')});
  [...doc().querySelectorAll('.nfd-button')].find(b=>b.textContent==='稍后再玩').click();await wait();
  const saved=window.__nfdPreviewCalls.find(c=>c[0]==='playLater')?.[1]?.item;
  checks.push({name:source+' preserves acquisition when saved',pass:saved?.source_id===source&&saved?.acquisition==='external'});
  checks.push({name:source+' explains acquisition',pass:!!doc().querySelector('.nfd-external-copy .nfd-message')?.textContent});
 }
 __nfdPreviewShow('switch',1280,800);await wait();
 checks.push({name:'Switch shows 20 homebrew fixture cards',pass:doc().querySelectorAll('.freedeck-library-grid-card').length===20});
 __nfdPreviewShow('discover',1280,800);await wait();
 [...doc().querySelectorAll('.nfd-tab-bar .nfd-button')].find(b=>b.textContent==='来源').click();await wait();
 const browse=[...doc().querySelectorAll('.nfd-button')].filter(b=>b.textContent==='浏览此来源的游戏');
 checks.push({name:'three public source browse actions',pass:browse.length===3});
 browse[0].click();await wait();
 checks.push({name:'source browse opens filtered pool',pass:doc().querySelector('.nfd-tab-bar .is-active')?.textContent==='资源池'&&doc().querySelectorAll('.freedeck-library-grid-card').length===10});
 window.__nfdCatalogReport={environment:'Steam native UI, fixture backend and navigation',checks};return checks;
})()
