import * as XLSX from 'xlsx';

export class UserError extends Error {}
const uid=p=>p+crypto.randomUUID().replaceAll('-','').slice(0,12);
const now=()=>new Date().toISOString();
const clone=v=>structuredClone(v);
const clean=v=>v===undefined||v===null?'':String(v).trim();
const norm=v=>clean(v).toLowerCase().replace(/[\s★♠◈()（）_\-]/g,'');
const assert=(ok,message)=>{if(!ok)throw new UserError(message);};
export const digest=async blob=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',blob))).map(n=>n.toString(16).padStart(2,'0')).join('');

const aliases={
 account:['사업자','계정','ID','3ID','쇼핑몰ID'],channel:['판매처','쇼핑몰','2쇼핑몰','쇼핑몰명','2쇼핑몰\n◈◈공구-쇼핑몰(셀러명)\n으로 변경'],
 number:['주문번호','사방넷주문번호','원주문번호','고객주문번호'],line_key:['주문행번호','주문행ID','상세주문번호','주문상세번호'],
 sku:['상품코드','품번코드','사방넷재고코드','1사방넷재고코드','재고코드','1사방넷\n재고코드(품번코드)\n♠4번파일 만들때\n필터-색기준정렬'],
 product:['상품명','품목명','수건명',"상품명\n♠♠4번파일 복붙시 '핸드워시' 류 제외"],option:['옵션','옵션명','실주문옵션명','색상'],quantity:['수량','주문수량'],
 recipient:['수취인','수취인명','받는분','받는분이름','이름'],phone:['연락처','폰','휴대폰','수취인전화1','받는분전화번호','수취인휴대폰','전화'],
 address:['주소','배송지','배송주소','받는분주소'],message:['메시지','메세지','배송메시지','배송메세지','배송요청사항'],source_status:['주문상태','상태'],
 reference:['배송ID','접수참조키','배송참조키'],tracking:['송장번호','운송장번호','운송장'],carrier:['택배사','배송업체'],
};
const lookup=Object.fromEntries(Object.entries(aliases).flatMap(([k,a])=>a.map(v=>[norm(v),k])));
export const defaultProfile={carrier:'CJ대한통운',validated:false,validation_note:'',shipping_columns:[['수취인','recipient'],['휴대폰','phone'],['전화','phone'],['주소','address'],['상품명','product'],['수량','quantity'],['배송메시지','message'],['쇼핑몰_사업자','channel_account'],['송하인','sender_name'],['송하인주소','sender_address'],['송하인휴대폰','sender_phone'],['송하인전화','sender_phone'],['공란','blank'],['사방넷주문번호','number']],tracking_columns:[['ID','account'],['쇼핑몰','channel'],['사방넷주문번호','number'],['택배사','carrier'],['송장번호','tracking']]};
const fields=new Set(['recipient','phone','address','product','quantity','message','channel_account','sender_name','sender_address','sender_phone','blank','number','reference','account','channel','carrier','tracking']);
export const newState=()=>({settings:{profile:clone(defaultProfile),mappings:[],senders:[]},batches:[],orders:[],shipments:[],artifacts:[],imports:[],tracking_rows:[],events:[]});

export function readWorkbook(bytes,kind='orders'){
 assert(bytes.length<=8*1024*1024,'웹 버전은 8MB 이하 XLSX 파일을 지원합니다.');
 let w;try{w=XLSX.read(bytes,{type:'array',cellFormula:true,cellDates:false,sheetRows:5011,bookVBA:true});}catch{throw new UserError('XLSX 파일을 읽을 수 없습니다. 암호·구형 XLS는 지원하지 않습니다.');}
 assert(!w.vbaraw,'매크로 파일은 지원하지 않습니다.');
 const required=kind==='orders'?['number','sku','quantity','recipient','phone','address']:['tracking'];let matches=[];
 for(const name of w.SheetNames){
  const sheet=w.Sheets[name],rows=XLSX.utils.sheet_to_json(sheet,{header:1,defval:'',raw:true});let best;
  rows.slice(0,10).forEach((row,index)=>{const map={};row.slice(0,100).forEach((v,c)=>{const f=lookup[norm(v)];if(f&&map[f]===undefined)map[f]=c;});if(required.every(f=>map[f]!==undefined)&&(!best||kind==='tracking'&&Object.keys(map).length>Object.keys(best.map).length))best={name,header:index,map,rows,sheet};});
  if(best)matches.push(best);
 }
 if(matches.length>1){const p=matches.filter(m=>['사방넷주문서','주문','주문입력','송장결과'].includes(m.name));if(p.length===1)matches=p;}
 assert(matches.length===1,'지원하는 헤더 시트를 찾지 못했거나 여러 개입니다. 입력 양식에 값만 넣어 저장하세요.');
 const m=matches[0],records=[];
 for(let i=m.header+1;i<m.rows.length;i++){
  const row=m.rows[i],record={};for(const [f,c] of Object.entries(m.map)){
   const cell=m.sheet[XLSX.utils.encode_cell({r:i,c})];assert(!cell?.f,`${i+1}행에 수식이 있습니다. 값으로 저장하세요.`);
   if(['number','line_key','reference','tracking'].includes(f)&&typeof row[c]==='number')assert(Number.isSafeInteger(row[c])&&String(row[c]).length<=15,`${i+1}행 식별자를 원천에서 텍스트로 다시 받으세요.`);
   record[f]=clean(row[c]);assert(record[f].length<=10000,'셀 내용이 너무 깁니다. 필요한 주문 데이터만 넣으세요.');
  }
  if(Object.values(record).some(Boolean)){record._row=i+1;records.push(record);}
 }
 assert(records.length>0,'헤더 아래에 데이터가 없습니다.');assert(records.length<=3000,'웹 버전은 파일당 3,000행 이하를 지원합니다.');
 return [records,{sheet:m.name,header_row:m.header+1,columns:Object.keys(m.map)}];
}

export function workbook(headers,rows,title='목록'){
 const safe=v=>typeof v==='string'&&/^[\s]*[=+\-@]/.test(v)?"'"+v:v??'';
 const sheet=XLSX.utils.aoa_to_sheet([headers,...rows.map(r=>r.map(safe))]);sheet['!cols']=headers.map((h,i)=>({wch:Math.min(48,Math.max(14,clean(h).length*2+3,...rows.slice(0,50).map(r=>Math.min(clean(r[i]).length*1.5+3,48))))}));sheet['!autofilter']={ref:sheet['!ref']};
 const w=XLSX.utils.book_new();XLSX.utils.book_append_sheet(w,sheet,title.slice(0,31));return new Uint8Array(XLSX.write(w,{type:'array',bookType:'xlsx',compression:true}));
}

export class Operations{
 constructor(state){this.s=state;this.files=new Map();}
 event(order_id,batch_id,action,detail){this.s.events.push({id:uid('E-'),order_id,batch_id,action,detail:JSON.stringify(detail),created:now()});}
 settings(data){
  assert(data&&data.profile&&Array.isArray(data.mappings)&&Array.isArray(data.senders),'기준정보 구조를 확인하세요.');const p=data.profile;
  assert(clean(p.carrier)&&typeof p.validated==='boolean','택배사·검증 여부를 확인하세요.');assert(!p.validated||clean(p.validation_note),'실제 접수 검증일·담당자·결과를 기록하세요.');
  for(const key of ['shipping_columns','tracking_columns']){
   const cols=p[key];assert(Array.isArray(cols)&&cols.length>0&&cols.length<=60,'출력 열은 1~60개로 지정하세요.');
   assert(cols.every(c=>Array.isArray(c)&&c.length===2&&clean(c[0])&&fields.has(c[1])),'출력 열의 헤더와 지원 필드를 확인하세요.');
   const required=key==='shipping_columns'?['recipient','phone','address','product','quantity','sender_name','sender_address','sender_phone']:['number','tracking','carrier'];assert(required.every(f=>cols.some(c=>c[1]===f)),'출력 양식에서 필수 배송·송장 필드가 빠졌습니다.');
  }
  assert(p.shipping_columns.some(c=>['number','reference'].includes(c[1])),'주문번호 또는 배송 참조키가 필요합니다.');let seen=new Set();
  for(const m of data.mappings){assert(['account','sku','internal_code','product','color'].every(k=>clean(m[k])),'상품 매핑의 사업자·코드·품명·색상을 입력하세요.');const key=JSON.stringify([m.account,m.channel??'*',m.sku,m.option??'']);assert(!seen.has(key),'중복 상품·옵션 매핑입니다.');seen.add(key);assert(['multiplier','max_units'].every(k=>Number.isInteger(m[k])&&m[k]>0&&m[k]<=100000),'구성수량과 박스 최대는 양의 정수여야 합니다.');}
  seen=new Set();for(const s of data.senders){assert(['account','name','phone','address'].every(k=>clean(s[k])),'송하인 정보의 필수값을 채우세요.');assert(!seen.has(s.account),'송하인 사업자가 중복됐습니다.');seen.add(s.account);}
  this.s.settings=clone(data);for(const o of this.s.orders)if(!this.shipment(o))o.reviewed=0;
  this.event(null,null,'기준정보 변경',{상품수:data.mappings.length,택배사:p.carrier});return {ok:true};
 }
 shipment(o){return this.s.shipments.find(s=>s.order_id===o.id&&s.revision===o.revision&&s.active);}
 importOrders(bytes,name,account,day,period,hash){
  assert(clean(account)&&account.length<=100,'사업자/계정을 입력하세요.');assert(/^\d{4}-\d{2}-\d{2}$/.test(day)&&!isNaN(Date.parse(day)),'기준일을 확인하세요.');assert(['오전','오후','추가'].includes(period),'시간대를 선택하세요.');
  const old=this.s.batches.find(b=>b.account===account&&b.hash===hash);if(old)return {batch_id:old.id,duplicate:true};
  const [rows,metadata]=readWorkbook(bytes);const grouped=new Map();for(const row of rows){assert(!row.account||row.account===account,`${row._row}행의 사업자가 선택한 사업자와 다릅니다.`);assert(row.number&&row.channel,`${row._row}행 주문번호·판매처가 없습니다.`);row.account=account;const key=JSON.stringify([row.channel,row.number]);if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(row);}
  const b={id:uid('B-'),name,account,day,period,hash,metadata,created:now(),order_ids:[]};
  const fingerprint=payload=>JSON.stringify(payload.lines.map(l=>JSON.stringify(Object.fromEntries(Object.keys(l).filter(k=>k!=='_row').sort().map(k=>[k,l[k]])))).sort());
  for(const [key,lines] of grouped){const [channel,number]=JSON.parse(key),payload={lines};let o=this.s.orders.find(o=>o.account===account&&o.channel===channel&&o.number===number);
   if(o){if(fingerprint(o.payload)!==fingerprint(payload)){if(this.shipment(o)){o.pending=payload;this.event(o.id,b.id,'생성 후 변경 감지',{조치:'기존 접수 확인 필요'});}else{o.payload=payload;o.revision++;o.reviewed=0;o.general_override=0;this.event(o.id,b.id,'원본 변경 반영',{조치:'재검수'});}o.updated=now();}else this.event(o.id,b.id,'중첩 주문 확인',{조치:'기존 주문 연결・중복 출고 없음'});}
   else{o={id:uid('O-'),account,channel,number,payload,revision:1,reviewed:0,review_note:'',manual_hold:'',general_override:0,pending:null,created:now(),updated:now()};this.s.orders.push(o);this.event(o.id,b.id,'주문 입력',{파일:name,원본행:lines.map(l=>l._row)});}
   b.order_ids.push(o.id);
  }
  this.s.batches.push(b);this.files.set('originals/'+b.id+'.xlsx',bytes);return {batch_id:b.id,duplicate:false,orders:grouped.size};
 }
 evaluate(o){
  let issues=[],total=0,lines=[],processing=false,cancelled=false;const addresses=new Set(),keys=[];
  if(o.pending)issues.push('생성 후 원본 변경: 기존 접수 취소·수정 확인 필요');if(o.manual_hold)issues.push('담당자 보류: '+o.manual_hold);
  for(const raw of o.payload.lines){let errs=[];for(const [f,l] of [['recipient','수취인'],['phone','연락처'],['address','주소']])if(!clean(raw[f]))errs.push(l+' 누락');
   if(raw.phone&&!/^[0-9]{8,15}$/.test(raw.phone.replace(/\D/g,'')))errs.push('연락처 형식 확인');addresses.add(JSON.stringify([raw.recipient,raw.phone,raw.address]));
   const valid=/^[1-9]\d{0,6}$/.test(raw.quantity),q=valid?Number(raw.quantity):0;if(!valid)errs.push('수량은 양의 정수여야 함');
   let candidates=this.s.settings.mappings.filter(m=>m.account===o.account&&m.sku===raw.sku&&(m.option??'')===(raw.option??'')&&['*',o.channel].includes(m.channel??'*'));const exact=candidates.filter(m=>m.channel===o.channel);if(exact.length)candidates=exact;const mapping=candidates.length===1?candidates[0]:null;if(!mapping)errs.push('미매핑 상품·옵션');
   const units=q*(mapping?.multiplier||1);total+=units;
   if(/자수|전사|포토|라벨|고리.?제거|스티커/.test([raw.product,raw.option,raw.message].join(' '))||mapping?.processing)processing=true;
   if(/변경|취소|분할|합포장|나눠|따로.?배송|박스.?\d|\d.?박스/.test(raw.message||''))errs.push('배송 메시지의 변경·포장 요청 확인');if(/취소|환불/.test(raw.source_status||''))cancelled=true;
   keys.push(raw.line_key||[raw.sku,raw.option||''].join('|'));lines.push({...clone(raw),quantity_value:q,units,mapping:clone(mapping),issues:errs});issues.push(...errs);
  }
  if(addresses.size>1)issues.push('한 주문의 수취 정보가 서로 다름');if(keys.length!==new Set(keys).size)issues.push('반복 주문행 식별자: 원천 주문행번호 확인');if(processing&&!o.general_override)issues.push('가공 의심 주문: 일반 출고 여부 확인');if(cancelled)issues.push('취소·환불 주문: 별도 확인');
  const max=Math.min(...lines.filter(l=>l.mapping).map(l=>l.mapping.max_units));if(Number.isFinite(max)&&total>max)issues.push('한 박스 최대 수량 초과: 복수 박스 수동 처리');let sender=this.s.settings.senders.find(s=>s.account===o.account);if(!sender)issues.push('사업자 송하인 정보 미등록');
  let ship=this.shipment(o),stage=0,status='검수 대기',next_action='주문 검수 후 확정';
  if(ship?.payload.order_snapshot){const snap=ship.payload.order_snapshot;lines=clone(snap.lines);total=snap.units;sender=clone(snap.sender);issues=[];if(o.pending)issues.push('생성 후 원본 변경: 기존 접수 취소·수정 확인 필요');if(o.manual_hold)issues.push('담당자 보류: '+o.manual_hold);}
  issues=[...new Set(issues)];if(issues.length){status='보류';next_action=issues[0];}else if(o.reviewed){stage=1;status='생성 준비';next_action='배치 결과물 생성';}
  if(ship){stage=2;status='파일 생성';next_action='택배 접수 후 외부접수 확인';if(ship.accepted_at){stage=3;status='외부접수 확인';next_action='송장 결과파일 업로드';}if(ship.tracking){stage=4;status='송장 연결';next_action=ship.accepted_at?'송장 반영파일 생성':'외부접수 확인 기록';}if(ship.reflected_at){stage=5;status='외부반영 확인';next_action='실제 출고 확인';}if(ship.shipped_at&&ship.reflected_at){status='처리 완료';next_action='완료';}if(o.pending||o.manual_hold){status='변경·보류';next_action=issues[0];}}
  const {payload,pending,...base}=o;return {...base,lines,issues,units:total,sender,stage,status,next_action,pending_change:Boolean(pending),shipment:ship?clone(ship):null};
 }
 updateOrder(id,data){
  const note=clean(data.note),o=this.s.orders.find(o=>o.id===id);assert(note,'확인자와 처리 사유를 입력하세요.');assert(o,'주문을 찾을 수 없습니다.');const ship=this.shipment(o),action=data.action;
  if(action==='resolve_change'){assert(o.pending,'대기 중인 변경이 없습니다.');assert(data.external_resolved,'기존 외부 접수 취소·수정 완료 여부를 확인하세요.');for(const s of this.s.shipments.filter(s=>s.order_id===id))s.active=0;o.payload=o.pending;o.pending=null;o.revision++;o.reviewed=0;o.general_override=0;o.manual_hold='';}
  else if(['accept','reflect','ship'].includes(action)){
   assert(ship&&!o.pending&&!o.manual_hold,'유효한 배송이 없거나 변경·보류 중입니다.');if(action==='reflect'){assert(ship.tracking,'송장 연결을 먼저 완료하세요.');assert(ship.accepted_at,'외부접수 확인을 먼저 기록하세요.');assert(this.s.artifacts.some(a=>a.kind==='tracking'&&a.order_versions.some(v=>v.id===id&&v.revision===o.revision)),'송장 반영파일을 먼저 생성하세요.');}if(action==='ship')assert(ship.accepted_at,'외부접수 확인을 먼저 기록하세요.');ship[{accept:'accepted_at',reflect:'reflected_at',ship:'shipped_at'}[action]]=now();
  }else if(action==='hold'){o.manual_hold=note;o.reviewed=0;}else if(action==='release'){o.manual_hold='';o.reviewed=0;}
  else if(['review','edit'].includes(action)){
   assert(!ship,'파일 생성 후 직접 수정할 수 없습니다. 변경된 원본을 다시 입력하세요.');
   if(action==='edit'){this.event(id,null,'수정 전 데이터',o.payload);for(const line of o.payload.lines)for(const k of ['recipient','phone','address','message'])if(data[k]!==undefined)line[k]=clean(data[k]);if(data.lines){assert(data.lines.length===o.payload.lines.length,'행 수는 변경할 수 없습니다.');o.payload.lines.forEach((l,i)=>{for(const k of ['sku','option','quantity','line_key'])if(data.lines[i][k]!==undefined)l[k]=clean(data.lines[i][k]);});}o.reviewed=0;o.general_override=Number(Boolean(data.general_override));}
   else{const v=this.evaluate(o);assert(!v.issues.length,'보류 사유를 먼저 해결하세요: '+v.issues.join(' / '));o.reviewed=1;o.review_note=note;}
  }else throw new UserError('지원하지 않는 작업입니다.');
  o.updated=now();this.event(id,null,({review:'검수 확정',edit:'주문 수정',hold:'담당자 보류',release:'보류 해제',accept:'외부접수 확인',reflect:'외부반영 확인',ship:'실제 출고 확인',resolve_change:'변경 해소・새 버전'})[action],{확인:note});return {ok:true};
 }
 batch(id){const b=this.s.batches.find(b=>b.id===id);assert(b,'배치를 찾을 수 없습니다.');return b;}
 reviewBatch(id,note){assert(clean(note),'검수 확인 내용을 입력하세요.');const b=this.batch(id);let reviewed=0,excluded=0;for(const o of this.s.orders.filter(o=>b.order_ids.includes(o.id))){if(this.shipment(o)||o.reviewed||this.evaluate(o).issues.length){excluded++;continue;}o.reviewed=1;o.review_note=note;o.updated=now();this.event(o.id,id,'일괄 검수 확정',{확인:note});reviewed++;}return {reviewed,excluded};}
 artifact(bid,kind,headers,rows,orders,metadata={}){
  const id=uid('F-'),labels={review:'출고검수',summary:'품목별출고집계',shipping:'택배등록',hold:'보류목록',tracking:'송장반영'},b=this.batch(bid);
  const prefix=['shipping','tracking'].includes(kind)&&!metadata.validated?'검증용_':'';const name=`${prefix}${b.day}_${b.account.replace(/[\\/:*?"<>|]/g,'_').slice(0,40)}_${b.period}_${bid}_${labels[kind]}_${id.slice(-4)}.xlsx`;
  this.files.set('artifacts/'+id+'.xlsx',workbook(headers,rows,labels[kind]));this.s.artifacts.push({id,batch_id:bid,kind,name,created:now(),metadata:clone(metadata),order_versions:orders.map(o=>({id:o.id,revision:o.revision}))});return id;
 }
 generate(bid,note){
  assert(clean(note),'생성 담당자와 대상·제외 주문 확인 내용을 입력하세요.');const b=this.batch(bid),profile=clone(this.s.settings.profile),ready=[],holds=[];let excluded=0;
  for(const o of this.s.orders.filter(o=>b.order_ids.includes(o.id))){const v=this.evaluate(o);if(this.shipment(o)){excluded++;continue;}(v.issues.length||!o.reviewed?holds:ready).push(v);}
  if(!ready.length){const existing=this.s.artifacts.filter(a=>a.batch_id===bid&&a.kind!=='tracking').map(a=>a.id);if(existing.length)return {artifacts:existing,reused:true,message:'새 생성 대상이 없습니다. 기존 결과물을 사용하세요.'};throw new UserError('검수 확정된 새 주문이 없습니다.');}
  const review=[],shipping=[],summary=new Map();for(const o of ready){const id=uid('S-'),first=o.lines[0],sender=o.sender;
   const values={...clone(first),quantity:o.units,reference:id,number:o.number,account:o.account,channel:o.channel,channel_account:o.channel+'_'+o.account,product:o.lines.map(l=>`${l.mapping.product} ${l.mapping.color} ${l.units}개`).join(' / '),sender_name:sender.name,sender_phone:sender.phone,sender_address:sender.address,blank:'',carrier:profile.carrier,order_snapshot:{lines:clone(o.lines),units:o.units,sender:clone(sender)}};
   this.s.shipments.push({id,order_id:o.id,revision:o.revision,batch_id:bid,carrier:profile.carrier,payload:values,profile:clone(profile),active:1,accepted_at:null,tracking:null,linked_at:null,reflected_at:null,shipped_at:null});
   for(const l of o.lines){const m=l.mapping;review.push([o.number,o.account,o.channel,id,l.sku,m.internal_code,m.product,m.color,l.quantity_value,l.units,first.recipient,first.phone,first.address,first.message||'',o.revision]);const key=JSON.stringify([m.internal_code,m.product,m.color]);summary.set(key,(summary.get(key)||0)+l.units);}
   shipping.push(profile.shipping_columns.map(([,f])=>values[f]??''));this.event(o.id,bid,'출고파일 생성',{배송ID:id,확인:note});
  }
  const metadata={validated:profile.validated,profile,rules_snapshot:clone(this.s.settings),generated_orders:ready.length,excluded_existing:excluded,hold_count:holds.length,note};
  const artifacts=[this.artifact(bid,'review',['주문번호','사업자','판매처','배송ID','판매상품코드','내부품목코드','품목명','색상','주문수량','출고수량','수취인','연락처','주소','메시지','버전'],review,ready,metadata),this.artifact(bid,'summary',['내부품목코드','품목명','색상','출고수량'],[...summary].map(([k,v])=>[...JSON.parse(k),v]),ready,metadata),this.artifact(bid,'shipping',profile.shipping_columns.map(c=>c[0]),shipping,ready,metadata),this.artifact(bid,'hold',['주문번호','사업자','판매처','보류사유','버전'],holds.map(o=>[o.number,o.account,o.channel,o.issues.join(' / ')||'검수 미확정',o.revision]),holds,metadata)];
  return {artifacts,generated:ready.length,holds:holds.length,reused:false};
 }
 importTracking(bytes,name,bid,hash){
  const b=this.batch(bid),old=this.s.imports.find(i=>i.batch_id===bid&&i.hash===hash);if(old)return {import_id:old.id,duplicate:true};const [rows]=readWorkbook(bytes,'tracking'),imp={id:uid('I-'),batch_id:bid,name,created:now(),hash};this.s.imports.push(imp);
  const ships=this.s.shipments.filter(s=>s.active&&b.order_ids.includes(s.order_id));const candidates=rows.map(row=>{
   row.tracking=(row.tracking||'').replace(/[\s-]/g,'');let matches=ships.filter(s=>{const o=this.s.orders.find(o=>o.id===s.order_id);return row.reference?s.id===row.reference:row.number?o.number===row.number:false;});
   for(const f of ['number','account','channel','carrier'])if(row[f])matches=matches.filter(s=>(f==='carrier'?s.carrier:this.s.orders.find(o=>o.id===s.order_id)[f])===row[f]);
   let issue=!/^\d{8,20}$/.test(row.tracking)?'송장번호 형식 확인':matches.length!==1?'유일한 주문 식별자가 없습니다. 직접 연결하세요.':'';
   if(matches.length===1){const o=this.s.orders.find(o=>o.id===matches[0].order_id);if(o.pending||o.manual_hold)issue='변경·보류 주문은 연결할 수 없습니다.';}
   return {row,matches,issue};
  });let linked=0;
  for(const c of candidates){if(candidates.filter(x=>x.row.tracking===c.row.tracking).length>1)c.issue='파일 안에 중복 송장번호가 있습니다.';if(c.matches.length===1&&candidates.filter(x=>x.matches.length===1&&x.matches[0].id===c.matches[0].id).length>1)c.issue='한 배송에 여러 송장행이 있습니다.';
   const r={id:uid('T-'),import_id:imp.id,payload:c.row,status:'unresolved',issue:c.issue,shipment_id:null};this.s.tracking_rows.push(r);if(!c.issue){try{this.link(r.id,c.matches[0].id,'식별자 자동 대조');linked++;}catch(e){r.issue=e.message;}}
  }
  this.files.set('originals/'+imp.id+'.xlsx',bytes);return {import_id:imp.id,linked,unresolved:rows.length-linked,duplicate:false};
 }
 link(rid,sid,note){
  assert(clean(note),'연결 확인자와 대조 근거를 입력하세요.');const r=this.s.tracking_rows.find(r=>r.id===rid),s=this.s.shipments.find(s=>s.id===sid&&s.active);assert(r&&s,'송장행 또는 배송을 찾을 수 없습니다.');assert(r.status!=='linked','이미 연결한 송장행입니다.');const i=this.s.imports.find(i=>i.id===r.import_id),b=this.batch(i.batch_id),o=this.s.orders.find(o=>o.id===s.order_id);assert(b.order_ids.includes(o.id),'다른 배치의 배송입니다.');assert(!o.pending&&!o.manual_hold,'변경·보류 주문은 연결할 수 없습니다.');const t=r.payload.tracking;
  assert(!r.payload.carrier||r.payload.carrier===s.carrier,'결과파일의 택배사가 배송과 다릅니다.');assert(/^\d{8,20}$/.test(t),'송장번호는 숫자 8~20자리여야 합니다.');assert(!s.tracking||s.tracking===t,'기존 송장이 있습니다. 덮어쓰지 않습니다.');assert(!this.s.shipments.some(x=>x.active&&x.id!==s.id&&x.carrier===s.carrier&&x.tracking===t),'다른 주문에 사용한 송장번호입니다.');assert(!this.s.tracking_rows.some(x=>x.import_id===r.import_id&&x.id!==r.id&&x.payload.tracking===t),'파일 내 중복 송장을 정리해 다시 입력하세요.');
  s.tracking=t;s.linked_at=now();r.status='linked';r.issue='';r.shipment_id=sid;this.event(o.id,b.id,'송장 연결',{송장번호:t,근거:note,결과행:rid});return {ok:true};
 }
 exportTracking(bid,note){
  assert(clean(note),'생성 확인자를 입력하세요.');const b=this.batch(bid),ships=this.s.shipments.filter(s=>{const o=this.s.orders.find(o=>o.id===s.order_id);return s.active&&s.tracking&&b.order_ids.includes(o.id)&&!o.pending&&!o.manual_hold;});assert(ships.length,'송장 연결이 완료된 유효한 주문이 없습니다.');const profiles=[...new Set(ships.map(s=>JSON.stringify(s.profile)))];assert(profiles.length===1,'양식 버전이 섞여 있습니다. 생성 배치별로 나눠 주세요.');const profile=JSON.parse(profiles[0]),fingerprint=JSON.stringify(ships.map(s=>[s.id,s.tracking]).sort());const old=this.s.artifacts.find(a=>a.batch_id===bid&&a.kind==='tracking'&&a.metadata.fingerprint===fingerprint);if(old)return {artifact_id:old.id,reused:true};
  const orders=ships.map(s=>({id:s.order_id,revision:s.revision})),rows=ships.map(s=>{const values={...s.payload,tracking:s.tracking,carrier:s.carrier};return profile.tracking_columns.map(([,f])=>values[f]??'');});const aid=this.artifact(bid,'tracking',profile.tracking_columns.map(c=>c[0]),rows,orders,{validated:profile.validated,profile,fingerprint,note});for(const s of ships)this.event(s.order_id,bid,'송장 반영파일 생성',{파일ID:aid,확인:note});return {artifact_id:aid,reused:false};
 }
 dashboard(){
  const orders=[...this.s.orders].reverse().sort((a,b)=>b.updated.localeCompare(a.updated)).map(o=>{
   const v=this.evaluate(o);v.batch_ids=this.s.batches.filter(b=>b.order_ids.includes(o.id)).map(b=>b.id);v.artifacts=this.s.artifacts.flatMap(a=>a.order_versions.filter(x=>x.id===o.id).map(x=>({id:a.id,revision:x.revision})));v.tracking_exported=this.s.artifacts.some(a=>a.kind==='tracking'&&a.order_versions.some(x=>x.id===o.id&&x.revision===o.revision));if(v.shipment){if(v.shipment.tracking&&!v.pending_change&&!v.manual_hold&&v.shipment.accepted_at&&!v.shipment.reflected_at)v.next_action=v.tracking_exported?'업로드·송신 후 외부반영 확인':'송장 반영파일 생성';delete v.shipment.payload;delete v.shipment.profile;}return v;
  });
  return {orders,batches:[...this.s.batches].reverse(),artifacts:[...this.s.artifacts].reverse().map(({order_versions,...a})=>a),tracking_rows:[...this.s.tracking_rows].reverse().map(r=>{const i=this.s.imports.find(i=>i.id===r.import_id);return {...r,batch_id:i.batch_id,name:i.name};}),events:[...this.s.events].reverse().slice(0,500),settings:clone(this.s.settings),data_path:'보호된 웹 데이터베이스 · 파일 저장소'};
 }
 detail(id){const o=this.s.orders.find(o=>o.id===id);assert(o,'주문을 찾을 수 없습니다.');return {pending_payload:o.pending,events:this.s.events.filter(e=>e.order_id===id).reverse()};}
}

export const orderHeaders=['사업자','판매처','주문번호','주문행번호','상품코드','상품명','옵션','수량','수취인','연락처','주소','메시지','주문상태'];
export function demoSettings(){return {profile:clone(defaultProfile),mappings:[{account:'데모사업자',channel:'*',sku:'DEMO-200-W',option:'화이트',internal_code:'T200-W',product:'호텔타올 200g',color:'화이트',multiplier:1,max_units:20,processing:false},{account:'데모사업자',channel:'*',sku:'DEMO-170-G',option:'그레이',internal_code:'T170-G',product:'데일리타올 170g',color:'그레이',multiplier:1,max_units:25,processing:false}],senders:[{account:'데모사업자',name:'충무 데모',phone:'01000000000',address:'대전광역시 데모구 샘플로 1'}]};}
export function demoOrders(){const rows=[];for(let i=1;i<=12;i++)rows.push(['데모사업자','스마트스토어',`DEMO-${String(i).padStart(4,'0')}`,`L-${i}`,i%2?'DEMO-200-W':'DEMO-170-G',i%2?'호텔타올':'데일리타올',i%2?'화이트':'그레이',2+i%4,`샘플고객${String(i).padStart(2,'0')}`,'01000000000',`대전광역시 데모구 샘플로 ${i}`,'','정상']);rows[8][4]='UNMAPPED-001';rows[9][10]='';rows[10][11]='자수 문구 확인 요청';rows[11][7]=40;return workbook(orderHeaders,rows,'주문입력');}
export function template(kind){if(kind==='orders')return [workbook(orderHeaders,[],'주문입력'),'주문입력_양식.xlsx'];if(kind==='demo_orders')return [demoOrders(),'샘플주문_가상데이터.xlsx'];if(kind==='tracking')return [workbook(['배송ID','주문번호','판매처','택배사','송장번호'],[],'송장결과'),'송장결과_양식.xlsx'];throw new UserError('없는 양식입니다.');}
export async function seedDemo(op){
 if(op.s.batches.length)return {ok:true,workspace:'demo'};op.settings(demoSettings());const bytes=demoOrders(),bid=op.importOrders(bytes,'가상주문_시연용.xlsx','데모사업자','2026-11-06','오전',await digest(bytes)).batch_id;const orders=[...op.s.orders].sort((a,b)=>a.number.localeCompare(b.number));
 for(const o of orders.slice(0,5))op.updateOrder(o.id,{action:'review',note:'데모 담당자 / 수량·배송정보 대조'});op.generate(bid,'데모 담당자 / 정상5건과 제외 주문 대조');for(const o of orders.slice(0,4))op.updateOrder(o.id,{action:'accept',note:'가상 접수 확인 / 실제 접수 아님'});
 const tracking=workbook(['배송ID','송장번호'],orders.slice(0,3).map((o,i)=>[op.shipment(o).id,'99000000000'+(i+1)]),'송장결과');op.importTracking(tracking,'가상송장_시연용.xlsx',bid,await digest(tracking));op.exportTracking(bid,'가상 송장 대조 완료');for(const o of orders.slice(0,2))op.updateOrder(o.id,{action:'reflect',note:'가상 외부반영 확인'});op.updateOrder(orders[0].id,{action:'ship',note:'가상 출고 확인'});for(const o of orders.slice(5,7))op.updateOrder(o.id,{action:'review',note:'가상 검수 확인'});return {ok:true,workspace:'demo'};
}
