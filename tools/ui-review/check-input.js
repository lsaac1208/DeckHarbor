(async()=>{
 const wait=()=>new Promise(r=>setTimeout(r,500));const checks=[];
 const doc=()=>document.getElementById('nfd-layout-preview').contentDocument;
 const send=(element,type,button)=>{const e=new CustomEvent(type,{bubbles:true,cancelable:true,detail:{button,source:0}});element.dispatchEvent(e);return e.defaultPrevented;};
 __nfdPreviewShow('home',320,800);await wait();doc().querySelector('.nfd-tab-bar .nfd-button').click();await wait();
 send(doc().querySelector('.nfd-tab-content .nfd-button'),'vgp_onbuttondown',DFL.GamepadButton.BUMPER_RIGHT);await wait();
 checks.push({name:'R1 from content switches tab',pass:doc().querySelector('.nfd-tab-bar .is-active')?.textContent==='我的游戏'});
 send(doc().querySelector('.nfd-tab-content .nfd-button'),'vgp_onbuttondown',DFL.GamepadButton.BUMPER_LEFT);await wait();
 checks.push({name:'L1 returns to home tab',pass:doc().querySelector('.nfd-tab-bar .is-active')?.textContent==='首页'});
 __nfdPreviewShow('settings',1280,800);await wait();window.__nfdPreviewNavigation='';
 send(doc().querySelector('.nfd-button'),'vgp_oncancel',DFL.GamepadButton.CANCEL);await wait();
 // Steam uses vgp_oncancel for the onCancelButton hook in this client.
 checks.push({name:'B navigates back',pass:window.__nfdPreviewNavigation==='back'});
 window.__nfdInputReport={environment:'Synthetic Steam gamepad events, not physical controller input',checks};return checks;
})()
