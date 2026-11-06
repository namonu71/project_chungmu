import { Operations, UserError, digest, template, seedDemo } from './operations.mjs';
import { load, mutate, fileObject } from './storage.mjs';
import { zipSync, strToU8 } from 'fflate';
const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
const download=(body,name,type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')=>new Response(body,{headers:{'Content-Type':type,'Content-Disposition':"attachment; filename*=UTF-8''"+encodeURIComponent(name),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
const base64Bytes=value=>{try{const raw=atob(value);if(raw.length>8*1024*1024)throw new Error();return Uint8Array.from(raw,c=>c.charCodeAt(0));}catch{throw new UserError('파일을 읽을 수 없습니다. 웹 버전은 8MB 이하 XLSX를 지원합니다.');}};

export async function handle(request,env){
 try{
  const url=new URL(request.url),path=url.pathname,workspace=url.searchParams.get('workspace')||request.headers.get('X-Workspace')||'live';if(!['live','demo'].includes(workspace))throw new UserError('작업 공간을 확인하세요.');
  if(request.method==='GET'){
   if(path.startsWith('/api/templates/')){const [bytes,name]=template(path.split('/').at(-1));return download(bytes,name);}
   if(path.startsWith('/api/files/')){const {file,object}=await fileObject(env,workspace,path.split('/').at(-1));return download(object.body,file.name,file.kind==='backup'?'application/zip':undefined);}
   const {state}=await load(env,workspace),op=new Operations(state);if(path==='/api/state')return json(op.dashboard());if(path.startsWith('/api/orders/'))return json(op.detail(path.split('/').at(-1)));return json({error:'주소를 찾을 수 없습니다.'},404);
  }
  if(request.method!=='POST')return json({error:'지원하지 않는 요청입니다.'},405);
  // Sites dispatch provides authentication for the owner-private Site.
  // The custom header and origin check also reject cross-origin mutations.
  if(request.headers.get('Origin')!==url.origin||request.headers.get('X-Local-Token')!=='hosted-same-origin')return json({error:'화면을 새로고침한 뒤 다시 시도하세요.'},403);
  if(!request.headers.get('Content-Type')?.includes('application/json'))return json({error:'JSON 요청이 필요합니다.'},415);
  const text=await request.text();if(text.length>12*1024*1024)throw new UserError('8MB 이하 XLSX 파일을 사용하세요.');const body=JSON.parse(text);
  const file=()=>{if(!body.name?.toLowerCase().endsWith('.xlsx'))throw new UserError('XLSX 파일만 지원합니다.');return base64Bytes(body.file);};
  if(path==='/api/demo')return json(await mutate(env,'demo',seedDemo));
  if(path==='/api/backup')return json(await mutate(env,workspace,async op=>{
   const id='Z-'+crypto.randomUUID().replaceAll('-','').slice(0,12),archive={'workspace.json':strToU8(JSON.stringify(op.s,null,2))};let size=0;const keys=[...op.s.batches.map(b=>'originals/'+b.id+'.xlsx'),...op.s.imports.map(i=>'originals/'+i.id+'.xlsx'),...op.s.artifacts.map(a=>'artifacts/'+a.id+'.xlsx')];
   for(const key of keys){const obj=await env.BUCKET.get(workspace+'/'+key);if(!obj)continue;size+=obj.size;if(size>30*1024*1024)throw new UserError('전체 백업이 30MB를 넘습니다. 결과물을 개별 다운로드해 주세요.');archive[key]=new Uint8Array(await obj.arrayBuffer());}
   op.files.set('backups/'+id+'.zip',zipSync(archive,{level:6}));const name='충무_웹업무백업_'+new Date().toISOString().slice(0,10)+'_'+id+'.zip';op.s.backups.push({id,kind:'backup',name,created:new Date().toISOString()});return {ok:true,path:'웹 파일 저장소에 백업 완료',download:'/api/files/'+id+'?workspace='+workspace};
  }));
  const result=await mutate(env,workspace,async op=>{
   if(path==='/api/import-orders'){const bytes=file();return op.importOrders(bytes,body.name,body.account||'',body.day||'',body.period||'',await digest(bytes));}
   if(path==='/api/settings')return op.settings(body);if(path==='/api/order')return op.updateOrder(body.id,body);if(path==='/api/review-batch')return op.reviewBatch(body.batch_id,body.note);if(path==='/api/generate')return op.generate(body.batch_id,body.note);
   if(path==='/api/import-tracking'){const bytes=file();return op.importTracking(bytes,body.name,body.batch_id,await digest(bytes));}
   if(path==='/api/link-tracking')return op.link(body.row_id,body.shipment_id,body.note);if(path==='/api/export-tracking')return op.exportTracking(body.batch_id,body.note);throw new UserError('지원하지 않는 작업입니다.');
  });return json(result);
 }catch(error){if(error instanceof UserError||error instanceof SyntaxError)return json({error:error.message},400);console.error('operations request failed',error);return json({error:'저장소 처리 중 오류가 발생했습니다. 잠시 후 새로고침해 주세요.'},500);}
}
