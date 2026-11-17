import fs from 'node:fs';
import path from 'node:path';
const output=process.argv[2];if(!output)throw new Error('Pass a private working directory for HTML snapshots');fs.mkdirSync(output,{recursive:true});
const targets=await (await fetch('http://127.0.0.1:8080/json/list')).json();
const target=targets.find(t=>t.title==='SharedJSContext');const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});let id=0;const pending=new Map();
ws.onmessage=e=>{const m=JSON.parse(e.data);if(pending.has(m.id)){const {resolve,reject}=pending.get(m.id);pending.delete(m.id);m.error?reject(m.error):resolve(m.result);}};
const send=(method,params)=>new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}));});
const pages=[['discover','discover',1280,800,''],['library','library',1280,800,''],['pc','discover',1280,800,'PC游戏'],['emulators','emulator',1280,800,''],['switch','switch',1280,800,''],['sources','discover',1280,1500,'来源'],['settings','settings',1280,800,''],['details','external-flathub',1280,800,''],['qam','home',360,800,''],['about','home',360,800,'关于']];
try {for(const [name,page,width,height,tab] of pages){
 const expression=`(async()=>{__nfdPreviewShow(${JSON.stringify(page)},${width},${height});await new Promise(r=>setTimeout(r,1200));const d=document.getElementById('nfd-layout-preview').contentDocument;const tab=${JSON.stringify(tab)};if(tab){[...d.querySelectorAll('.nfd-tab-bar .nfd-button')].find(b=>b.textContent.trim()===tab)?.click();await new Promise(r=>setTimeout(r,900));}if(d.querySelector('[data-preview-error]'))throw new Error(d.body.innerText);const styles=[...d.styleSheets].map(s=>{try{return [...s.cssRules].map(r=>r.cssText).join(' ')}catch(e){return ''}}).join(' ');return '<!doctype html><html><head><meta charset="utf-8"><style>'+styles+'</style></head>'+d.body.outerHTML+'</html>';})()`;
 const result=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));
 let html=result.result.value;const assets=new URL('../../defaults/emulator_assets/',import.meta.url);for(const file of fs.readdirSync(assets)){const mime=file.endsWith('.svg')?'image/svg+xml':file.endsWith('.png')?'image/png':'image/webp';html=html.replaceAll('http://127.0.0.1:20065/emulator_icons/'+file,'data:'+mime+';base64,'+fs.readFileSync(new URL(file,assets)).toString('base64'));}fs.writeFileSync(path.join(output,name+'.html'),html);console.log(name,width,height);
}fs.writeFileSync(path.join(output,'pages.json'),JSON.stringify(pages));}finally{ws.close();}
