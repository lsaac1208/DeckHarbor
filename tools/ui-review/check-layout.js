(async()=>{
 const wait=(ms=400)=>new Promise(r=>setTimeout(r,ms));const checks=[];
 const doc=()=>document.getElementById('nfd-layout-preview').contentDocument;
 function measure(name){
  const d=doc(),root=d.querySelector('.nfd-ui');const errors=[];
  if(!root)errors.push(d.body.innerText.slice(0,250));
  const fault=d.querySelector('[data-preview-error]');if(fault)errors.push(fault.textContent.slice(0,350));
  if(root&&root.scrollWidth>root.clientWidth+1)errors.push(`root overflows ${root.scrollWidth}/${root.clientWidth}`);
  for(const el of d.querySelectorAll('.nfd-button,.nfd-select,.nfd-library-item,.nfd-section,.nf-dlcard,.freedeck-library-grid-card,.freedeck-library-collection-card')){
   const r=el.getBoundingClientRect();let clip=d.documentElement.getBoundingClientRect();
   for(let p=el.parentElement;p&&p!==d.documentElement;p=p.parentElement){const cs=d.defaultView.getComputedStyle(p);if(['auto','scroll'].includes(cs.overflowX)){clip={left:-Infinity,right:Infinity};break;}if(cs.overflowX==='hidden'){clip=p.getBoundingClientRect();break;}}
   if(r.width>0&&(r.right>clip.right+1||r.left<clip.left-1)&&!el.closest('.nfd-tab-bar'))errors.push(`overflow ${el.className}: ${r.x}/${r.width}`);
  }
  // Buttons inside a card/toolbar must not overlap their siblings.
  for(const group of d.querySelectorAll('.nfd-game-actions,.nfd-header-actions,.nfd-actions,.nfd-settings-nav')){
   const boxes=[...group.querySelectorAll(':scope > .nfd-button')].map(e=>e.getBoundingClientRect());
   for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];if(Math.min(a.right,b.right)>Math.max(a.left,b.left)+1&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)+1)errors.push('overlapping buttons');}
  }
  checks.push({name,errors:[...new Set(errors)]});
 }
 for(const [width,height] of [[1280,800],[1280,720],[800,600]]){
  for(const page of ['library','settings','emulator','discover','login','sources','switch','external-steam','external-flathub','external-homebrew']){
   __nfdPreviewShow(page,width,height);await wait(650);measure(`${page} ${width}x${height}`);
   if(page==='settings')for(const button of [...doc().querySelectorAll('.nfd-settings-nav .nfd-button')]){button.click();await wait();measure(`settings/${button.textContent} ${width}`);}
   if(page==='discover')for(const button of [...doc().querySelectorAll('.nfd-tab-bar .nfd-button')]){button.click();await wait(700);measure(`discover/${button.textContent} ${width}`);}
  }
 }
 for(const width of [300,360]){
  __nfdPreviewShow('home',width,800);await wait();
  __nfdPreviewLoad('downloadStore').setDownloadQueue([0,1,2].map(i=>({task_id:'preview'+i,game_title:'非常长的下载游戏名称 '+i,status:i===2?'error':'paused',error:'测试错误：'+('长路径/'.repeat(25)),progress:35,speed:2000})));
  await wait();measure(`QAM downloads ${width}`);
  [...doc().querySelectorAll('.nfd-button')].find(b=>b.textContent.includes('查看全部'))?.click();await wait();measure(`QAM expanded downloads ${width}`);
  for(const b of [...doc().querySelectorAll('.nfd-tab-bar .nfd-button')]){b.click();await wait();measure(`QAM/${b.textContent} ${width}`);}
 }
 window.__nfdPreviewFailSettings=true;__nfdPreviewShow('settings',1280,800);await wait();
 checks.push({name:'settings failure is recoverable',errors:doc().body.innerText.includes('重新加载')?[]:['no retry']});
 window.__nfdPreviewFailSettings=false;[...doc().querySelectorAll('.nfd-button')].find(b=>b.textContent==='重新加载')?.click();await wait();
 checks.push({name:'settings retry succeeds',errors:doc().querySelector('.nfd-settings-nav')?[]:['retry failed']});
 window.__nfdLayoutReport={environment:'Steam native DFL controls + isolated backend fixtures; desktop hidden renderer, not physical gamepad validation',checks};
 return {total:checks.length,failed:checks.filter(c=>c.errors.length)};
})()
