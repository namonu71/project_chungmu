/* 이미지·텍스트·CSV 주문 입력.
 * 파일 → 주문 행 추출(텍스트 구조 인식, 이미지는 브라우저 OCR) → 담당자 검토·보정
 * → 기존 주문 입력(/api/import-extracted)으로 넘겨 XLSX와 같은 검수·결과물 생성 흐름을 탄다.
 * app.js의 전역(state, api, toast, refresh, esc, $, requireOperator, filter, view)을 사용한다. */
const EXTRACT_FIELDS=[['account','사업자'],['channel','판매처'],['number','주문번호'],['line_key','주문행번호'],['sku','상품코드'],['product','상품명'],['option','옵션'],['quantity','수량'],['recipient','수취인'],['phone','연락처'],['address','주소'],['message','메시지'],['source_status','주문상태']];
const EXTRACT_REQUIRED=new Set(['channel','number','sku','quantity','recipient','phone','address']);
const EXTRACT_ORDER_LEVEL=['account','channel','number','recipient','phone','address','message'];
const EXTRACT_ALIASES={
 account:['사업자','계정','ID','쇼핑몰ID'],channel:['판매처','쇼핑몰','쇼핑몰명','채널','판매채널','마켓'],
 number:['주문번호','사방넷주문번호','원주문번호','고객주문번호','주문 번호'],line_key:['주문행번호','주문행ID','상세주문번호','주문상세번호'],
 sku:['상품코드','품번코드','사방넷재고코드','재고코드','품번','제품코드'],product:['상품명','품목명','수건명','품명','제품명','상품'],
 option:['옵션','옵션명','실주문옵션명','색상'],quantity:['수량','주문수량','개수'],
 recipient:['수취인','수취인명','받는분','받는분이름','받는사람','이름','성함'],phone:['연락처','폰','휴대폰','수취인전화1','받는분전화번호','수취인휴대폰','전화','전화번호','핸드폰','휴대전화'],
 address:['주소','배송지','배송주소','받는분주소','배송지주소','받는주소'],message:['메시지','메세지','배송메시지','배송메세지','배송요청사항','요청사항','배송요청'],source_status:['주문상태','상태'],
};
const extractNorm=v=>String(v??'').trim().toLowerCase().replace(/[\s★♠◈()（）_\-.]/g,'');
const EXTRACT_LOOKUP=Object.fromEntries(Object.entries(EXTRACT_ALIASES).flatMap(([k,a])=>a.map(v=>[extractNorm(v),k])));
const EXTRACT_MAX=8*1024*1024;
const OCR_BASE='https://cdn.jsdelivr.net/npm/';
const extractTypes={jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',webp:'image/webp',txt:'text/plain',tsv:'text/plain',csv:'text/csv'};

function extractMime(file){const ext=file.name.split('.').pop().toLowerCase();if(extractTypes[ext])return extractTypes[ext];return Object.values(extractTypes).includes(file.type)?file.type:'';}

// CSV/TSV with quoted cells (including line breaks inside quotes).
function parseDelimited(text,d){
 const rows=[];let row=[],cell='',quoted=false;
 for(let i=0;i<text.length;i++){const c=text[i];
  if(quoted){if(c==='"'){if(text[i+1]==='"'){cell+='"';i++;}else quoted=false;}else cell+=c;continue;}
  if(c==='"'&&cell.trim()===''){quoted=true;cell='';}else if(c===d){row.push(cell.trim());cell='';}else if(c==='\n'){row.push(cell.trim());rows.push(row);row=[];cell='';}else cell+=c;
 }
 row.push(cell.trim());rows.push(row);return rows.filter(r=>r.some(Boolean));
}
function headerMap(cells){const map={};cells.forEach((c,i)=>{const f=EXTRACT_LOOKUP[extractNorm(c)];if(f&&!(f in map))map[f]=i;});return map;}
function tableRecords(text){
 let best=null;
 for(const d of ['\t',',','|',';',/\s{2,}/]){
  const rows=d instanceof RegExp?text.split('\n').map(l=>l.trim().split(d).map(s=>s.trim())).filter(r=>r.some(Boolean)):parseDelimited(text,d);
  for(let h=0;h<Math.min(rows.length,10);h++){const map=headerMap(rows[h]);const n=Object.keys(map).length;
   if(n>=3&&(!best||n>best.n)){best={n,records:rows.slice(h+1).map(r=>Object.fromEntries(Object.entries(map).map(([f,i])=>[f,r[i]??''])))};break;}}
 }
 return best?.records.filter(r=>Object.values(r).some(Boolean))||null;
}
// "주문번호: 1234" style blocks. A repeated field starts the next record. A blank line only
// separates orders when an item is complete and a new order begins (OCR splits paragraphs freely).
function keyValueRecords(text){
 const out=[];let cur={},soft=false;const flush=()=>{if(Object.keys(cur).length>=2)out.push(cur);cur={};};
 for(const raw of text.split('\n')){const line=raw.replace(/^\s*[-*•·▶■□○●]\s*/,'').trim();
  if(!line){soft=true;continue;}
  let field,value;const m=/^([^:：=]{1,24}?)\s*[:：=]\s*(.*)$/.exec(line);
  if(m&&EXTRACT_LOOKUP[extractNorm(m[1])]){field=EXTRACT_LOOKUP[extractNorm(m[1])];value=m[2];}
  else{const t=line.split(/\s+/);for(const k of [2,1]){if(t.length>k&&EXTRACT_LOOKUP[extractNorm(t.slice(0,k).join(''))]){field=EXTRACT_LOOKUP[extractNorm(t.slice(0,k).join(''))];value=t.slice(k).join(' ');break;}}}
  if(!field)continue;if(cur[field]||(soft&&cur.sku&&(field==='number'||field==='channel')))flush();soft=false;cur[field]=value.trim();
 }
 flush();return out.length?out:null;
}
function tidyRecords(records,defaults){
 let prev=null;
 return records.map(r=>{const rec=Object.fromEntries(EXTRACT_FIELDS.map(([f])=>[f,String(r[f]??'').trim()]));
  // Item lines that follow an order without their own order number belong to that order.
  if(!rec.number&&prev)for(const f of EXTRACT_ORDER_LEVEL)if(!rec[f])rec[f]=prev[f];
  if(rec.quantity){const q=rec.quantity.replace(/[,\s]/g,'').match(/\d+/);if(q)rec.quantity=q[0];}
  if(rec.phone)rec.phone=rec.phone.replace(/[^\d\-+]/g,'');
  for(const [f,v] of Object.entries(defaults))if(!rec[f]&&v)rec[f]=v;
  prev=rec;return rec;});
}
function extractFromText(text,defaults){
 text=String(text||'').replace(/^﻿/,'').replace(/\r\n?/g,'\n');
 const found=tableRecords(text)||keyValueRecords(text);
 return {rows:tidyRecords(found||[],defaults),recognized:!!found};
}
async function decodeText(file){const buf=await file.arrayBuffer();try{return new TextDecoder('utf-8',{fatal:true}).decode(buf);}catch{return new TextDecoder('euc-kr').decode(buf);}}
function readDataUrl(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(new Error('파일을 읽지 못했습니다.'));r.readAsDataURL(file);});}

let ocrLoader=null;
function loadOcr(){
 if(window.Tesseract)return Promise.resolve(window.Tesseract);
 return ocrLoader??=new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=OCR_BASE+'tesseract.js@5.1.1/dist/tesseract.min.js';s.integrity='sha384-GJqSu7vueQ9qN0E9yLPb3Wtpd7OrgK8KmYzC8T1IysG1bcvxvIO4qtYR/D3A991F';s.crossOrigin='anonymous';
  s.onload=()=>resolve(window.Tesseract);s.onerror=()=>{ocrLoader=null;s.remove();reject(new Error('OCR 모듈을 불러오지 못했습니다. 인터넷 연결을 확인하세요.'));};document.head.appendChild(s);});
}
async function runOcr(file,progress){
 const T=await loadOcr();
 const worker=await T.createWorker('kor+eng',1,{workerPath:OCR_BASE+'tesseract.js@5.1.1/dist/worker.min.js',corePath:OCR_BASE+'tesseract.js-core@5.1.1',logger:m=>{if(m.status)progress(m.status==='recognizing text'?`글자 인식 중 ${Math.round((m.progress||0)*100)}%`:'OCR 준비 중 (처음 한 번은 언어 데이터를 내려받습니다)');}});
 try{await worker.setParameters({preserve_interword_spaces:'1'});const {data}=await worker.recognize(file);return data.text;}finally{await worker.terminate();}
}

let extractJob=null;
function ensureExtractDialog(){
 let d=$('#extract-dialog');if(d)return d;
 d=document.createElement('dialog');d.id='extract-dialog';d.className='extract-dialog';
 d.innerHTML=`<form id="extract-form" novalidate><div class="dialog-heading"><div><div class="eyebrow">EXTRACTED ORDERS</div><h2>추출 결과 검토</h2></div><button type="button" class="icon-button" data-extract="close" aria-label="닫기">×</button></div>
 <p class="muted" id="extract-summary"></p>
 <div class="extract-body"><div class="extract-source"><div id="extract-preview"></div><label>추출 원문 <span class="muted">(오인식된 글자를 고친 뒤 다시 추출할 수 있습니다)</span><textarea id="extract-text" rows="9" spellcheck="false"></textarea></label><button type="button" class="ghost" data-extract="reparse">원문으로 다시 추출</button></div>
 <div class="extract-rows"><div class="extract-table-wrap"><table class="settings-table extract-table"><thead><tr>${EXTRACT_FIELDS.map(([f,l])=>`<th>${l}${EXTRACT_REQUIRED.has(f)?' <b class="req">*</b>':''}</th>`).join('')}<th></th></tr></thead><tbody id="extract-tbody"></tbody></table></div><button type="button" class="ghost" data-extract="add">＋ 행 추가</button></div></div>
 <div class="notice">자동 추출 결과는 틀릴 수 있습니다. 원본과 대조해 주문번호·상품코드·수량·배송정보를 확인하세요. <b>검토 완료</b>를 누르면 엑셀 입력과 같은 매핑·검수 단계로 넘어가고, 원본 자료는 배치에 보관됩니다.</div>
 <div class="dialog-footer"><button type="button" class="ghost" data-extract="close">취소</button><button class="primary" type="submit">검토 완료 · 주문 입력</button></div><p class="form-error" role="alert"></p></form>`;
 document.body.appendChild(d);
 d.addEventListener('click',e=>{const b=e.target.closest('[data-extract]');if(!b)return;const a=b.dataset.extract;
  if(a==='close'){extractJob=null;d.close();}
  else if(a==='add'){captureExtractRows();extractJob.rows.push(tidyRecords([{}],extractJob.defaults)[0]);renderExtractRows();}
  else if(a==='del'){captureExtractRows();extractJob.rows.splice(Number(b.dataset.row),1);renderExtractRows();}
  else if(a==='reparse'){const r=extractFromText($('#extract-text').value,extractJob.defaults);extractJob.rows=r.rows.length?r.rows:tidyRecords([{}],extractJob.defaults);renderExtractRows();setExtractSummary(r.recognized?`${r.rows.length}개 행을 다시 추출했습니다.`:'항목 이름(주문번호, 수취인, 주소 등)을 찾지 못했습니다. 표에 직접 입력하세요.');}
 });
 d.addEventListener('input',e=>{if(e.target.matches('#extract-tbody input'))e.target.classList.toggle('missing',EXTRACT_REQUIRED.has(e.target.dataset.field)&&!e.target.value.trim());});
 d.querySelector('form').addEventListener('submit',submitExtraction);
 return d;
}
function setExtractSummary(text){$('#extract-summary').textContent=`${extractJob.file.name} · ${extractJob.defaults.account} · ${extractJob.defaults.day} ${extractJob.period} — ${text}`;}
function renderExtractRows(){
 $('#extract-tbody').innerHTML=extractJob.rows.map((r,i)=>`<tr>${EXTRACT_FIELDS.map(([f,l])=>`<td><input data-row="${i}" data-field="${f}" value="${esc(r[f])}" aria-label="${i+1}행 ${l}" class="${EXTRACT_REQUIRED.has(f)&&!r[f]?'missing':''}"></td>`).join('')}<td><button type="button" class="icon-button" data-extract="del" data-row="${i}" aria-label="${i+1}행 삭제">×</button></td></tr>`).join('')||`<tr><td colspan="${EXTRACT_FIELDS.length+1}" class="muted">행이 없습니다. ＋ 행 추가로 입력하세요.</td></tr>`;
}
function captureExtractRows(){for(const input of document.querySelectorAll('#extract-tbody input'))extractJob.rows[Number(input.dataset.row)][input.dataset.field]=input.value.trim();}

// Called from the order upload dialog for any non-XLSX file.
async function openExtraction(file,{account,day,period}){
 const mime=extractMime(file);
 if(!mime)throw new Error('XLSX, CSV, TXT, 이미지(JPG·PNG·WEBP) 파일만 입력할 수 있습니다.');
 if(file.size>EXTRACT_MAX)throw new Error('이미지·텍스트 자료는 8MB 이하만 입력할 수 있습니다.');
 const d=ensureExtractDialog(),job={file,mime,period,defaults:{account,day},rows:[],dataUrl:await readDataUrl(file)};extractJob=job;
 d.querySelector('.form-error').textContent='';
 const image=mime.startsWith('image/');
 $('#extract-preview').innerHTML=image?`<img src="${job.dataUrl}" alt="입력한 원본 이미지">`:'';
 $('#extract-text').value='';job.rows=tidyRecords([{}],job.defaults);renderExtractRows();
 setExtractSummary(image?'이미지에서 글자를 인식하는 중입니다…':'내용을 읽는 중입니다…');d.showModal();
 let text='';
 try{text=image?await runOcr(file,m=>{if(extractJob===job)setExtractSummary(m);}):await decodeText(file);}
 catch(err){if(extractJob===job)setExtractSummary((err.message||'자동 인식에 실패했습니다.')+' 원본을 보며 표에 직접 입력하세요.');return;}
 if(extractJob!==job)return;
 $('#extract-text').value=text;
 const r=extractFromText(text,job.defaults);if(r.rows.length)job.rows=r.rows;renderExtractRows();
 setExtractSummary(r.recognized?`${r.rows.length}개 행을 추출했습니다. 빨간 칸은 필수값입니다.`:'항목 이름(주문번호, 수취인, 주소 등)을 찾지 못했습니다. 원문을 고쳐 다시 추출하거나 표에 직접 입력하세요.');
}

async function submitExtraction(e){
 e.preventDefault();const form=e.target,error=form.querySelector('.form-error');error.textContent='';
 if(busy||!extractJob)return;
 try{
  requireOperator();captureExtractRows();
  const rows=extractJob.rows.filter(r=>Object.entries(r).some(([f,v])=>v&&!(f in extractJob.defaults)));
  if(!rows.length)throw new Error('입력할 주문 행이 없습니다.');
  const bad=rows.findIndex(r=>!r.number||!r.channel);if(bad>=0)throw new Error(`${extractJob.rows.indexOf(rows[bad])+1}행의 주문번호·판매처를 입력하세요.`);
  busy=true;form.classList.add('loading');
  const result=await api('/api/import-extracted',{name:extractJob.file.name,account:extractJob.defaults.account,day:extractJob.defaults.day,period:extractJob.period,headers:EXTRACT_FIELDS.map(([,l])=>l),rows:rows.map(r=>EXTRACT_FIELDS.map(([f])=>r[f])),source:{name:extractJob.file.name,mime:extractJob.mime,file:extractJob.dataUrl.split(',')[1]}});
  extractJob=null;$('#extract-dialog').close();filter={q:'',batch:result.batch_id,status:'all'};view='orders';
  toast(result.duplicate?'같은 내용의 기존 배치를 열었습니다.':`${result.orders}개 주문을 입력했습니다. 검수 단계를 진행하세요.`);
  await refresh();
 }catch(err){error.textContent=err.message;toast(err.message,true);}finally{busy=false;form.classList.remove('loading');}
}
