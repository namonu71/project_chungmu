import {Operations,newState,seedDemo,template,digest,UserError} from '../../web/lib/operations.mjs';
import {zipSync,strToU8} from 'fflate';

const LIVE='https://chungmu-order-operations.peppyagate1.chatgpt.site';
const DB_NAME='chungmu-github-pages-demo-v1';
const urls=new Map();
let db,queue=Promise.resolve();
function openDB(){return new Promise((resolve,reject)=>{const request=indexedDB.open(DB_NAME,1);request.onupgradeneeded=()=>request.result.createObjectStore('records');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(new Error('브라우저 저장소를 사용할 수 없습니다. 비공개 모드 설정을 확인하세요.'));});}
function read(key){return new Promise((resolve,reject)=>{const tx=db.transaction('records','readonly'),req=tx.objectStore('records').get(key);req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});}
function commit(state,files){return new Promise((resolve,reject)=>{const tx=db.transaction('records','readwrite'),store=tx.objectStore('records');store.put(state,'state');for(const [key,bytes] of files)store.put(bytes,key);tx.oncomplete=resolve;tx.onerror=()=>reject(new Error('브라우저 저장 공간이 부족하거나 저장하지 못했습니다.'));tx.onabort=()=>reject(new Error('저장 작업을 완료하지 못했습니다.'));});}
async function cacheFiles(state){for(const a of [...state.artifacts,...(state.backups||[])])if(!urls.has(a.id)){const bytes=await read((a.kind==='backup'?'backups/':'artifacts/')+a.id+(a.kind==='backup'?'.zip':'.xlsx'));if(bytes)urls.set(a.id,URL.createObjectURL(new Blob([bytes],{type:a.kind==='backup'?'application/zip':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})));}}
function bytes(body){if(!body.name?.toLowerCase().endsWith('.xlsx'))throw new UserError('XLSX 파일만 지원합니다.');let raw;try{raw=atob(body.file);}catch{throw new UserError('파일 내용을 읽지 못했습니다.');}if(raw.length>8*1024*1024)throw new UserError('8MB 이하 XLSX를 사용하세요.');return Uint8Array.from(raw,c=>c.charCodeAt(0));}

const ready=(async()=>{db=await openDB();let state=await read('state');if(!state){state=newState();state.backups=[];const op=new Operations(state);await seedDemo(op);await commit(state,op.files);}await cacheFiles(state);})();

async function execute(path,body){
 await ready;const state=await read('state'),op=new Operations(state);
 if(!body){if(path==='/api/state'){await cacheFiles(state);const result=op.dashboard();result.data_path='이 브라우저의 체험 데이터 · IndexedDB';return result;}if(path.startsWith('/api/orders/'))return op.detail(path.split('/').at(-1));throw new UserError('지원하지 않는 조회입니다.');}
 let result;
 if(path==='/api/demo'){result=await seedDemo(op);}
 else if(path==='/api/import-orders'){const data=bytes(body);result=op.importOrders(data,body.name,body.account||'',body.day||'',body.period||'',await digest(data));}
 else if(path==='/api/settings')result=op.settings(body);
 else if(path==='/api/order')result=op.updateOrder(body.id,body);
 else if(path==='/api/review-batch')result=op.reviewBatch(body.batch_id,body.note);
 else if(path==='/api/generate')result=op.generate(body.batch_id,body.note);
 else if(path==='/api/import-tracking'){const data=bytes(body);result=op.importTracking(data,body.name,body.batch_id,await digest(data));}
 else if(path==='/api/link-tracking')result=op.link(body.row_id,body.shipment_id,body.note);
 else if(path==='/api/export-tracking')result=op.exportTracking(body.batch_id,body.note);
 else if(path==='/api/backup'){
  const id='Z-'+crypto.randomUUID().slice(0,12),archive={'workspace.json':strToU8(JSON.stringify(state,null,2))};let size=0;
  for(const key of [...state.batches.map(b=>'originals/'+b.id+'.xlsx'),...state.imports.map(i=>'originals/'+i.id+'.xlsx'),...state.artifacts.map(a=>'artifacts/'+a.id+'.xlsx')]){const data=await read(key);if(data){size+=data.byteLength;if(size>30*1024*1024)throw new UserError('백업 크기가 30MB를 넘습니다. 결과물을 개별 다운로드하세요.');archive[key]=data;}}
  const name='충무_GitHubPages_체험백업_'+new Date().toISOString().slice(0,10)+'.zip';op.files.set('backups/'+id+'.zip',zipSync(archive));state.backups||=[];state.backups.push({id,kind:'backup',name,created:new Date().toISOString()});result={ok:true,path:'브라우저 체험 데이터 백업',download_id:id,name};
 }else throw new UserError('지원하지 않는 작업입니다.');
 await commit(state,op.files);await cacheFiles(state);if(result.download_id)result.download=urls.get(result.download_id);return result;
}

export const pagesApi={
 ready,
 request(path,body){const work=()=>navigator.locks?navigator.locks.request(DB_NAME,()=>execute(path,body)):execute(path,body);const next=queue.then(work);queue=next.catch(()=>{});return next;},
 fileUrl(id){return urls.get(id)||'#';},
};

document.addEventListener('click',async event=>{
 const live=event.target.closest('[data-action="live"]');
 if(live){event.preventDefault();event.stopImmediatePropagation();window.open(LIVE,'_blank','noopener');return;}
 const anchor=event.target.closest('a[href^="/api/templates/"]');
 if(anchor){event.preventDefault();event.stopImmediatePropagation();try{const [data,name]=template(anchor.getAttribute('href').split('/').at(-1));const url=URL.createObjectURL(new Blob([data],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}catch(error){alert(error.message);}}
},true);

document.addEventListener('click',event=>{
 const a=event.target.closest('a[href^="blob:"]');if(!a)return;
 const stateName=a.getAttribute('title');if(!a.hasAttribute('download'))a.download=stateName||'충무_체험결과.xlsx';
},true);
