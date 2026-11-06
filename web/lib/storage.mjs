import { newState, Operations, UserError } from './operations.mjs';
const kinds=['batches','orders','shipments','artifacts','imports','tracking_rows','events','backups'];
function flat(state){const out=new Map([['settings:main',{kind:'settings',id:'main',payload:JSON.stringify(state.settings)}]]);for(const k of kinds)for(const item of state[k]||[])out.set(k+':'+item.id,{kind:k,id:item.id,payload:JSON.stringify(item)});return out;}

export async function load(env,workspace){
 if(!env.DB||!env.BUCKET)throw new Error('DB/BUCKET bindings missing');
 await env.DB.prepare('INSERT OR IGNORE INTO workspace_revisions(workspace,revision) VALUES(?,0)').bind(workspace).run();
 const [version,records]=await env.DB.batch([env.DB.prepare('SELECT revision FROM workspace_revisions WHERE workspace=?').bind(workspace),env.DB.prepare('SELECT kind,id,payload FROM workspace_records WHERE workspace=?').bind(workspace)]);
 const state=newState();state.backups=[];for(const row of records.results||[]){if(row.kind==='settings')state.settings=JSON.parse(row.payload);else if(kinds.includes(row.kind))state[row.kind].push(JSON.parse(row.payload));}
 for(const k of ['batches','artifacts','imports','events'])state[k].sort((a,b)=>(a.created||'').localeCompare(b.created||''));
 return {state,revision:version.results[0].revision};
}

export async function mutate(env,workspace,operation){
 for(let attempt=0;attempt<4;attempt++){
  const {state,revision}=await load(env,workspace),before=flat(state),op=new Operations(state),result=await operation(op),after=flat(state),changes=[...after].filter(([k,v])=>before.get(k)?.payload!==v.payload).map(([,v])=>v);
  if(!changes.length&&!op.files.size)return result;const keys=[];
  try{
   for(const [key,bytes] of op.files){const full=workspace+'/'+key;await env.BUCKET.put(full,bytes,{httpMetadata:{contentType:key.endsWith('.zip')?'application/zip':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});keys.push(full);}
   const statements=[];for(let i=0;i<changes.length;i+=75)statements.push(env.DB.prepare(`INSERT OR REPLACE INTO workspace_records(workspace,kind,id,payload) SELECT ?,json_extract(value,'$.kind'),json_extract(value,'$.id'),json_extract(value,'$.payload') FROM json_each(?) WHERE EXISTS(SELECT 1 FROM workspace_revisions WHERE workspace=? AND revision=?)`).bind(workspace,JSON.stringify(changes.slice(i,i+75)),workspace,revision));
   statements.push(env.DB.prepare('UPDATE workspace_revisions SET revision=revision+1 WHERE workspace=? AND revision=?').bind(workspace,revision));const response=await env.DB.batch(statements);if(response.at(-1).meta.changes===1)return result;for(const key of keys)await env.BUCKET.delete(key);
  }catch(error){for(const key of keys)try{await env.BUCKET.delete(key);}catch{}throw error;}
 }
 throw new UserError('다른 작업이 동시에 저장 중입니다. 새로고침 후 다시 시도하세요.');
}

export async function fileObject(env,workspace,id){
 const {state}=await load(env,workspace),file=state.artifacts.find(a=>a.id===id)||state.backups.find(b=>b.id===id);if(!file)throw new UserError('파일을 찾을 수 없습니다.');const key=file.kind==='backup'?'backups/'+id+'.zip':'artifacts/'+id+'.xlsx',object=await env.BUCKET.get(workspace+'/'+key);if(!object)throw new UserError('저장된 파일이 없습니다.');return {file,object};
}
