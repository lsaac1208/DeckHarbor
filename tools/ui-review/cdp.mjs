import fs from 'node:fs';
const targets = await (await fetch('http://127.0.0.1:8080/json/list')).json();
const target = targets.find(t => t.title === 'SharedJSContext');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject) => {ws.onopen=resolve;ws.onerror=reject;});
let id=0; const pending=new Map();
ws.onmessage = e => {const m=JSON.parse(e.data); if(pending.has(m.id)){const {resolve,reject}=pending.get(m.id); pending.delete(m.id); m.error?reject(m.error):resolve(m.result);}};
function send(method,params={}){return new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}));});}
try {
 if(process.argv[3]) await send('Emulation.setDeviceMetricsOverride',{width:Number(process.argv[4]||1280),height:Number(process.argv[5]||800),deviceScaleFactor:1,mobile:false});
 const expression=fs.readFileSync(process.argv[2],'utf8');
 const result=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
 console.log(JSON.stringify(result,null,2));
 if(process.argv[3]) {const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:Number(process.argv[4]||1280),height:Number(process.argv[5]||800),scale:1}}); fs.writeFileSync(process.argv[3],Buffer.from(shot.data,'base64'));}
} finally {if(process.argv[3]) await send('Emulation.clearDeviceMetricsOverride');ws.close();}
