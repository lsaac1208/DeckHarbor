const fs=require('fs'),path=require('path'),ts=require('typescript');
const base=path.resolve(__dirname,'../../src');
const files={};
function walk(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,e.name);if(e.isDirectory())walk(p);else if(/\.tsx?$/.test(p)){const key=path.relative(base,p).replace(/\.tsx?$/,''); files[key]=ts.transpileModule(fs.readFileSync(p,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.React}}).outputText;}}}
walk(base);
const coverFile=process.env.DECKHARBOR_PREVIEW_COVERS;
const coverMap=coverFile?JSON.parse(fs.readFileSync(coverFile,'utf8')):{};
const setup=fs.readFileSync(path.join(__dirname,'preview-runtime.js'),'utf8');
fs.writeFileSync('/tmp/nfd-preview.js',`(async()=>{const docsPreview=${process.argv.includes("--docs")};const communityGames=${fs.readFileSync(path.resolve(__dirname,"../../defaults/community_games.json"),"utf8")}.games;if(docsPreview){const covers=${JSON.stringify(coverMap)};for(const g of communityGames)g.cover_url=covers[g.cover_url]||g.cover_url;}const sources=${JSON.stringify(files)};${setup}})()`);
