import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {realpathSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root=fileURLToPath(new URL('../',import.meta.url));
const req=createRequire(realpathSync(path.join(root,'web/node_modules/wrangler/package.json')));
const {build}=req('esbuild');
await mkdir(path.join(root,'pages/assets'),{recursive:true});
let html=await readFile(path.join(root,'automation/static/index.html'),'utf8');
html=html.replace('__TOKEN__','github-pages-demo').replace('<title>충무 | 주문 운영 대시보드</title>','<title>충무 프로젝트 | GitHub Pages 대시보드</title>')
 .replace('href="/styles.css"','href="./pages/assets/dashboard.css"').replace('src="/app.js"','src="./pages/assets/dashboard.js"').replace('href="/"','href="./index.html"')
 .replace('PC 로컬 작업 공간','GitHub Pages 체험 공간').replace('업무 데이터 백업','체험 데이터 백업').replace('XLSX · 최대 20MB','XLSX · 최대 8MB')
 .replace('가상 데이터로 보는 데모입니다. 실제 주문·접수·출고와 연결되지 않습니다.','GitHub Pages 체험판 · 가상 데이터로 시작하며, 변경 내용은 이 브라우저에만 저장됩니다.')
 .replace('실제 업무로 돌아가기 →','실제 업무용 웹앱 열기 ↗');
await writeFile(path.join(root,'index.html'),html);
await writeFile(path.join(root,'.nojekyll'),'');
let client=await readFile(path.join(root,'automation/static/app.js'),'utf8');
const start=client.indexOf('async function api('),end=client.indexOf('\nfunction toast',start);
if(start<0||end<0)throw new Error('Client API integration point missing');
client=client.slice(0,start)+'async function api(path,body){return pagesApi.request(path,body);}\n'+client.slice(end);
client="import {pagesApi} from './bridge.mjs';\n"+client;
client=client.replace(/let workspace=localStorage[^;]+;/,"let workspace='demo';")
 .replaceAll('chungmu.workspace','chungmu.pages.workspace').replaceAll('chungmu.operator','chungmu.pages.operator')
 .replace("const download=id=>`/api/files/${encodeURIComponent(id)}?workspace=${workspace}`;","const download=id=>pagesApi.fileUrl(id);")
 .replace("localStorage.getItem('chungmu.pages.operator')||''","localStorage.getItem('chungmu.pages.operator')||'체험 사용자'")
 .replaceAll('20*1024*1024','8*1024*1024').replaceAll('20MB 이하','8MB 이하')
 .replace("$('#workspace-toggle').textContent=workspace==='demo'?'실제 업무':'데모 보기'","$('#workspace-toggle').textContent='실제 업무용 웹앱 ↗'")
 .replace("toast('백업 저장 완료: '+r.path);","toast('체험 데이터 백업을 다운로드합니다.');if(r.download){const a=document.createElement('a');a.href=r.download;a.download=r.name;document.body.appendChild(a);a.click();a.remove();}")
 .replace("$('#last-updated').textContent='최근 확인 '","$('#last-updated').textContent='이 브라우저 · 최근 확인 '");
await writeFile(path.join(root,'pages/src/client.js'),client);
await writeFile(path.join(root,'pages/assets/dashboard.css'),await readFile(path.join(root,'automation/static/styles.css')));
await build({entryPoints:[path.join(root,'pages/src/client.js')],outfile:path.join(root,'pages/assets/dashboard.js'),bundle:true,format:'iife',platform:'browser',target:'es2022',minify:true,legalComments:'eof',nodePaths:[path.join(root,'web/node_modules')]});
console.log('GitHub Pages dashboard built: index.html');
