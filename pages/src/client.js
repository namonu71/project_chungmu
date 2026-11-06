import {pagesApi} from './bridge.mjs';
'use strict';
const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const token=$('meta[name="local-token"]').content;
let workspace='demo';
if(!['live','demo'].includes(workspace))workspace='live';
let state={orders:[],batches:[],artifacts:[],tracking_rows:[],events:[],settings:{mappings:[],senders:[],profile:{}}};
let view='dashboard',filter={q:'',batch:'',status:'all'},activeOrder=null,actionContext=null,settingsDraft=null,busy=false;
const titles={dashboard:['운영 대시보드','주문이 어디까지 진행됐는지, 어떤 결과물이 만들어졌는지 확인하세요.'],orders:['주문별 진행 현황','주문번호를 열면 단계별 진행과 결과물, 변경 이력을 함께 볼 수 있습니다.'],batches:['입력 배치','오전·오후 주문 파일을 관리하고 검수한 결과물을 생성하세요.'],tracking:['송장 대조','회수한 송장을 원주문에 연결하고, 미연결 건을 확인하세요.'],files:['생성 결과물','주문에 연결된 출고·택배·송장 파일과 생성 버전을 확인하세요.'],settings:['기준정보 · 양식','확정한 상품·옵션과 송하인, 접수 양식을 관리하세요.']};
const kindLabel={review:'출고검수',summary:'품목집계',shipping:'택배등록',hold:'보류목록',tracking:'송장반영'};
const steps=['주문 입력','검수 확정','출고파일 생성','외부접수 확인','송장 연결','외부반영 확인'];
const milestones=o=>[true,Boolean(o.reviewed||o.shipment),Boolean(o.shipment),Boolean(o.shipment?.accepted_at),Boolean(o.shipment?.tracking),Boolean(o.shipment?.reflected_at)];
const date=v=>v?new Date(v).toLocaleString('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
const num=v=>Number(v||0).toLocaleString('ko-KR');
const download=id=>pagesApi.fileUrl(id);
$('#operator').value=localStorage.getItem('chungmu.pages.operator')||'체험 사용자';
$('#operator').addEventListener('input',e=>localStorage.setItem('chungmu.pages.operator',e.target.value));

async function api(path,body){return pagesApi.request(path,body);}

function toast(text,error=false){const t=$('#toast');t.textContent=text;t.classList.toggle('error',error);t.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.hidden=true,error?9000:5000);}
async function refresh(){state=await api('/api/state');render();$('#last-updated').textContent='이 브라우저 · 최근 확인 '+new Date().toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'});}
function isHold(o){return o.pending_change||o.manual_hold||(!o.shipment&&o.issues.length>0);}
function badge(o){let cls=isHold(o)?'amber':o.status==='처리 완료'?'teal':o.stage>=4?'teal':o.stage>=2?'blue':'';return `<span class="badge ${cls}">${esc(o.status)}</span>`;}
function stageDots(o){const flags=milestones(o);return `<div class="stage-dots" title="${esc(steps[o.stage])}">${steps.map((s,i)=>`<span aria-label="${esc(s)} ${flags[i]?'완료':'대기'}" class="stage-dot ${flags[i]?(i===o.stage?isHold(o)?'hold':'current':'complete'):''}"></span>`).join('')}</div><div class="stage-caption">${esc(isHold(o)?'확인 필요':steps[o.stage])}</div>`;}
function orderArtifacts(o){return o.artifacts.map(a=>({...a,file:state.artifacts.find(f=>f.id===a.id)})).filter(a=>a.file);}
function filesPills(o){const arts=orderArtifacts(o).filter(a=>a.revision===o.revision);return arts.length?arts.map(a=>`<a class="artifact-pill" href="${download(a.id)}" title="${esc(a.file.name)}">${esc(kindLabel[a.file.kind])} ↓</a>`).join(''):'<span class="small-muted">아직 없음</span>';}
function batchOptions(all=true){return `${all?'<option value="">전체 배치</option>':''}${state.batches.map(b=>`<option value="${b.id}" ${filter.batch===b.id?'selected':''}>${esc(b.day)} ${esc(b.period)} · ${esc(b.account)} · ${b.id.slice(-4)}</option>`).join('')}`;}
function selectedOrders(){return state.orders.filter(o=>(!filter.batch||o.batch_ids.includes(filter.batch))&&(!filter.q||[o.number,o.account,o.channel,...o.lines.map(l=>l.recipient),o.shipment?.tracking].join(' ').toLowerCase().includes(filter.q.toLowerCase()))&&(filter.status==='all'||filter.status==='hold'&&isHold(o)||filter.status==='ready'&&o.stage===1&&!isHold(o)||filter.status==='progress'&&o.stage>=2&&o.status!=='처리 완료'&&!isHold(o)||filter.status==='done'&&o.status==='처리 완료'));}
function filtersHtml(){return `<div class="filters"><input id="order-search" type="search" placeholder="주문번호 · 수취인 · 송장번호 검색" value="${esc(filter.q)}" aria-label="주문 검색"><select id="batch-filter" aria-label="배치 필터">${batchOptions()}</select><select id="status-filter" aria-label="상태 필터">${[['all','전체 상태'],['hold','보류 · 확인 필요'],['ready','생성 준비'],['progress','처리 중'],['done','처리 완료']].map(([v,t])=>`<option value="${v}" ${filter.status===v?'selected':''}>${t}</option>`).join('')}</select><span class="count">${num(selectedOrders().length)}개 주문</span></div>`;}
function ordersTable(orders){return `<div class="table-scroll"><table class="order-table"><thead><tr><th>주문번호 / 사업자</th><th>수취인 / 수량</th><th>진행 단계</th><th>상태</th><th>생성 결과물</th><th>다음 작업</th><th></th></tr></thead><tbody>${orders.map(o=>`<tr><td><button class="order-number" data-action="detail" data-id="${o.id}">${esc(o.number)}</button><div class="sub">${esc(o.account)} · ${esc(o.channel)} · v${o.revision}</div></td><td>${esc(o.lines[0]?.recipient||'미입력')}<div class="sub">${num(o.units)}개 · ${o.lines.length}개 품목행</div></td><td>${stageDots(o)}</td><td>${badge(o)}</td><td style="min-width:145px;max-width:205px">${filesPills(o)}</td><td><div class="truncate" title="${esc(o.next_action)}">${esc(o.next_action)}</div>${o.shipment?.tracking?`<div class="sub">${esc(o.shipment.carrier)} ${esc(o.shipment.tracking)}</div>`:''}</td><td><button class="section-link" data-action="detail" data-id="${o.id}">상세 →</button></td></tr>`).join('')}</tbody></table>${!orders.length?'<div class="empty">선택한 조건에 맞는 주문이 없습니다.</div>':''}</div>`;}
function emptyWelcome(){return `<div class="card empty-state"><div class="symbol">▤</div><h2>첫 주문 파일을 입력해 보세요</h2><p>파일을 넣으면 상품·수량을 정리하고, 주문번호별 진행 현황을 보여드립니다.<br>실제 업무 전에는 가상 데이터로 전체 흐름을 살펴볼 수 있습니다.</p><div class="empty-actions"><button class="primary" data-action="upload">＋ 주문 파일 입력</button><button class="ghost" data-action="demo">가상 데이터로 둘러보기</button></div><div class="inline-links" style="justify-content:center"><a href="/api/templates/orders">입력 양식 다운로드 ↓</a><button class="section-link" data-view="settings">기준정보 먼저 설정 →</button></div><div class="empty-steps"><span><b>1</b> 상품·송하인 설정</span><span><b>2</b> 주문 입력·검수</span><span><b>3</b> 파일 생성·송장 대조</span></div></div>`;}
function dashboard(){
 const orders=state.orders,hold=orders.filter(isHold),files=state.artifacts,ready=orders.filter(o=>o.stage===1&&!isHold(o)),done=orders.filter(o=>o.status==='처리 완료');
 const stats=[['전체 주문',orders.length,'중복 입력은 같은 주문으로 추적','▤',''],['확인 필요한 주문',hold.length,'보류 사유를 확인하고 다음 작업 진행','!','warn'],['생성 준비',ready.length,'검수 확정 · 출고파일 생성 가능','↗',''],['생성 결과물',files.length,`처리 완료 ${done.length}건 · 원본과 버전 보관`,'▱','']];
 let html=`<div class="stats">${stats.map(([label,value,foot,icon,cls])=>`<div class="stat-card ${cls}"><span class="stat-icon">${icon}</span><div class="label">${label}</div><div class="value">${num(value)}<span class="unit">${label==='생성 결과물'?'개':'건'}</span></div><div class="foot">${foot}</div></div>`).join('')}</div>`;
 if(!orders.length)return html+emptyWelcome();
 html+=`<section class="card"><div class="card-heading"><div><h3>단계별 진행 현황</h3><p>각 단계의 실제 완료·확인 주문 수 · 전체 ${num(orders.length)}건 기준</p></div><span class="badge teal">${Math.round(done.length/orders.length*100)}% 처리 완료</span></div><div class="pipeline">${steps.map((label,i)=>`<div class="pipeline-step ${i>=4?'done':''}"><span class="index">0${i+1}</span><div class="count">${num(orders.filter(o=>milestones(o)[i]).length)}</div><div class="label">${label}</div></div>`).join('')}</div></section>`;
 html+=`<section class="card"><div class="card-heading"><div><h3>주문번호별 진행 현황</h3><p>주문을 선택하면 생성 결과물과 단계별 이력이 열립니다.</p></div><button class="section-link" data-view="orders">전체 주문 보기 →</button></div>${filtersHtml()}${ordersTable(selectedOrders().slice(0,8))}${selectedOrders().length>8?`<div class="warning-line">전체 ${selectedOrders().length}건 중 8건 표시 · 주문별 진행 현황에서 전체 확인</div>`:''}</section>`;
 html+=`<div class="split"><section class="card"><div class="card-heading"><h3>최근 생성 결과물</h3><button class="section-link" data-view="files">전체 보기 →</button></div>${files.slice(0,4).map(fileItem).join('')||'<div class="empty">검수 확정 후 파일을 생성하세요.</div>'}${!state.settings.profile.validated?'<div class="warning-line">현재 접수 양식은 검증 전입니다. 택배·송장반영 파일을 실제 접수 전에 확인하세요.</div>':''}</section><section class="card"><div class="card-heading"><h3>먼저 확인할 주문</h3><span class="badge amber">${hold.length}건</span></div>${hold.slice(0,4).map(o=>`<div class="attention-item"><span class="attention-dot"></span><div><button class="order-number" data-action="detail" data-id="${o.id}">${esc(o.number)}</button><p>${esc(o.next_action)}</p></div></div>`).join('')||'<div class="empty">현재 보류 중인 주문이 없습니다.</div>'}</section></div>`;
 return html;
}
function fileItem(f){const count=state.orders.filter(o=>o.artifacts.some(a=>a.id===f.id)).length;return `<div class="file-item"><span class="file-icon">XLSX</span><div><a href="${download(f.id)}">${esc(kindLabel[f.kind])}${!f.metadata.validated&&['shipping','tracking'].includes(f.kind)?' <span class="badge amber">검증용</span>':''}</a><div class="meta">${esc(f.batch_id)} · ${date(f.created)} · ${count}건 포함</div></div><a href="${download(f.id)}" class="download" aria-label="${esc(kindLabel[f.kind])} 다운로드">↓</a></div>`;}
function ordersPage(){return `<section class="card"><div class="card-heading"><h3>모든 주문 <span class="small-muted">${num(state.orders.length)}건</span></h3><span class="small-muted">단계 6개 + 실제 출고 별도 확인</span></div>${filtersHtml()}${ordersTable(selectedOrders())}</section>`;}
function batchesPage(){return `<div class="notice">출고파일 생성 시 검수 확정된 새 주문만 포함됩니다. 보류·미검수 주문과 기존 생성 주문은 별도로 집계하며, 중첩 배치에서도 중복 생성하지 않습니다.</div>${!state.batches.length?emptyWelcome():`<div class="batch-grid">${state.batches.map(b=>{const orders=state.orders.filter(o=>o.batch_ids.includes(b.id));return `<section class="card batch-card"><div class="eyebrow">${esc(b.id)}</div><h3>${esc(b.day)} ${esc(b.period)} · ${esc(b.account)}</h3><div class="batch-meta">${esc(b.name)} · ${date(b.created)}${b.metadata?.source?` · <a href="${download(b.metadata.source.id)}" title="${esc(b.metadata.source.name)}" download="${esc(b.metadata.source.name)}">원본 자료 ↓</a>`:''}</div><div class="batch-counts"><div><b>${orders.length}</b><span>전체 주문</span></div><div><b>${orders.filter(o=>o.stage===1&&!isHold(o)).length}</b><span>생성 준비</span></div><div><b>${orders.filter(isHold).length}</b><span>보류</span></div><div><b>${orders.filter(o=>o.shipment).length}</b><span>파일 생성</span></div></div><div class="batch-actions"><button class="ghost small-button" data-action="review-batch" data-id="${b.id}">정상 주문 일괄 검수</button><button class="primary small-button" data-action="generate" data-id="${b.id}">출고파일 생성</button><button class="ghost small-button" data-action="batch-orders" data-id="${b.id}">주문 보기</button><button class="ghost small-button" data-action="tracking-upload" data-id="${b.id}">송장 입력</button><button class="ghost small-button" data-action="export-tracking" data-id="${b.id}">반영파일 생성</button></div></section>`;}).join('')}</div>`}`;}
function trackingPage(){const rows=state.tracking_rows.filter(r=>!filter.batch||r.batch_id===filter.batch);return `<div class="toolbar"><select id="batch-filter" aria-label="배치 필터">${batchOptions()}</select><div class="heading-actions"><a class="ghost" href="/api/templates/tracking">입력 양식 ↓</a><button class="primary" data-action="tracking-upload">＋ 송장 결과 입력</button></div></div><div class="stats">${[['입력 송장행',rows.length],['연결 완료',rows.filter(r=>r.status==='linked').length],['직접 확인',rows.filter(r=>r.status!=='linked').length],['송장 미연결 주문',state.orders.filter(o=>(!filter.batch||o.batch_ids.includes(filter.batch))&&o.shipment&&!o.shipment.tracking).length]].map(([l,v])=>`<div class="stat-card"><div class="label">${l}</div><div class="value">${v}<span class="unit">건</span></div></div>`).join('')}</div><section class="card"><div class="card-heading"><h3>송장 결과 대조</h3><span class="small-muted">이름·전화번호만으로 자동 연결하지 않습니다.</span></div><div class="table-scroll"><table><thead><tr><th>송장번호</th><th>원본 식별자</th><th>상태</th><th>대조 결과</th><th>작업</th></tr></thead><tbody>${rows.map(r=>{const o=state.orders.find(o=>o.shipment?.id===r.shipment_id);return `<tr><td><b>${esc(r.payload.tracking)}</b><div class="sub">${esc(r.name)}</div></td><td>${esc(r.payload.number||r.payload.reference||'식별자 없음')}</td><td><span class="badge ${r.status==='linked'?'teal':'amber'}">${r.status==='linked'?'연결 완료':'직접 확인'}</span></td><td>${r.status==='linked'?`<button class="order-number" data-action="detail" data-id="${o?.id||''}">${esc(o?.number||r.shipment_id)}</button>`:esc(r.issue)}</td><td>${r.status!=='linked'?`<button class="ghost small-button" data-action="link" data-id="${r.id}">직접 연결</button>`:'—'}</td></tr>`;}).join('')}</tbody></table>${!rows.length?'<div class="empty">택배 시스템에서 내려받은 송장 결과를 입력하세요.</div>':''}</div></section><section class="card"><div class="card-heading"><h3>배치별 반영용 파일 생성</h3></div><div class="settings-section"><p>송장 대조가 끝난 주문만 포함합니다. 외부 시스템 업로드와 송신 성공은 별도로 확인해 기록하세요.</p><div class="batch-actions">${state.batches.map(b=>`<button class="ghost small-button" data-action="export-tracking" data-id="${b.id}">${esc(b.day)} ${esc(b.period)} · ${esc(b.account)} 반영파일</button>`).join('')}</div></div></section>`;}
function filesPage(){const files=state.artifacts.filter(f=>!filter.batch||f.batch_id===filter.batch);return `<div class="toolbar"><select id="batch-filter" aria-label="배치 필터">${batchOptions()}</select><span class="muted">${files.length}개 파일 · 이전 버전도 보관</span></div><section class="card"><div class="card-heading"><h3>모든 결과물</h3><span class="small-muted">파일명·생성 시각·포함 주문 추적</span></div><div class="table-scroll"><table><thead><tr><th>결과물</th><th>배치 / 생성 시각</th><th>연결 주문</th><th>검증</th><th>다운로드</th></tr></thead><tbody>${files.map(f=>{const orders=state.orders.filter(o=>o.artifacts.some(a=>a.id===f.id));return `<tr><td><b>${esc(kindLabel[f.kind])}</b><div class="sub">${esc(f.name)}</div></td><td>${esc(f.batch_id)}<div class="sub">${date(f.created)}</div></td><td>${orders.slice(0,3).map(o=>`<button class="order-number" data-action="detail" data-id="${o.id}">${esc(o.number)}</button>`).join('<br>')}${orders.length>3?`<div class="sub">외 ${orders.length-3}건</div>`:''}${!orders.length?'대상 없음':''}</td><td>${['shipping','tracking'].includes(f.kind)?`<span class="badge ${f.metadata.validated?'teal':'amber'}">${f.metadata.validated?'양식 검증 기록 있음':'접수 검증 전'}</span>`:'<span class="badge">내부 검수용</span>'}</td><td><a class="ghost small-button" href="${download(f.id)}">XLSX ↓</a></td></tr>`;}).join('')}</tbody></table>${!files.length?'<div class="empty">입력 배치에서 출고 결과물을 생성하면 여기에 나타납니다.</div>':''}</div></section>`;}
function settingsPage(){if(!settingsDraft)settingsDraft=structuredClone(state.settings);const s=settingsDraft;return `<div class="notice">기준정보를 변경하면 파일을 아직 생성하지 않은 주문은 다시 검수해야 합니다. 이미 생성된 파일과 배송은 당시 규칙을 유지합니다. 실데이터와 데모의 설정은 분리됩니다.</div><form id="settings-form"><section class="card"><div class="card-heading"><div><h3>상품·옵션 매핑</h3><p>사업자 + 판매처 + 상품코드 + 옵션을 확정 코드에 연결합니다. 판매처 *는 해당 사업자의 모든 판매처입니다.</p></div><button type="button" class="ghost small-button" data-action="add-mapping">＋ 상품 추가</button></div><div class="table-scroll"><table class="settings-table"><thead><tr>${['사업자','판매처','판매코드','옵션','내부코드','품명','색상','구성수량','박스최대','가공',''].map(t=>`<th>${t}</th>`).join('')}</tr></thead><tbody>${s.mappings.map((m,i)=>`<tr>${['account','channel','sku','option','internal_code','product','color','multiplier','max_units'].map(k=>`<td><input data-map="${i}" data-field="${k}" aria-label="${esc(k)}" value="${esc(m[k]??(k==='channel'?'*':''))}" ${['multiplier','max_units'].includes(k)?'type="number" min="1"':'type="text"'}></td>`).join('')}<td><input type="checkbox" data-map="${i}" data-field="processing" aria-label="가공 상품" ${m.processing?'checked':''}></td><td><button type="button" class="icon-button" data-action="remove-mapping" data-index="${i}" aria-label="매핑 삭제">×</button></td></tr>`).join('')}</tbody></table>${!s.mappings.length?'<div class="empty">상품 추가를 눌러 검증한 상품·옵션을 등록하세요.</div>':''}</div></section><section class="card"><div class="card-heading"><h3>사업자별 송하인</h3><button type="button" class="ghost small-button" data-action="add-sender">＋ 송하인 추가</button></div><div class="table-scroll"><table class="settings-table"><thead><tr><th>사업자</th><th>송하인명</th><th>연락처</th><th>주소</th><th></th></tr></thead><tbody>${s.senders.map((m,i)=>`<tr>${['account','name','phone','address'].map(k=>`<td><input data-sender="${i}" data-field="${k}" aria-label="${k}" value="${esc(m[k])}"></td>`).join('')}<td><button type="button" class="icon-button" data-action="remove-sender" data-index="${i}" aria-label="송하인 삭제">×</button></td></tr>`).join('')}</tbody></table></div></section><section class="card"><div class="settings-section"><h3>택배 접수·송장반영 양식</h3><p>초기 14열 접수 양식은 기존 엑셀의 복사 구간을 참고한 검증 전 양식입니다. 실제 접수에 맞춰 열 순서와 필드를 지정하고 시험 결과를 남기세요.</p><div class="form-grid two"><label>택배사<input id="profile-carrier" value="${esc(s.profile.carrier)}" required></label><label class="checkbox-label"><input id="profile-validated" type="checkbox" ${s.profile.validated?'checked':''}>실제 접수 양식 검증 완료</label></div><label>검증 근거<input id="profile-note" value="${esc(s.profile.validation_note)}" placeholder="검증일 · 담당자 · 택배/사방넷 접수 결과"></label><div class="form-grid two"><label>택배 등록 열 <span class="small-muted">[헤더, 필드] 목록 · 순서 유지</span><textarea id="shipping-columns" class="code-input">${esc(JSON.stringify(s.profile.shipping_columns,null,2))}</textarea></label><label>송장반영 열<textarea id="tracking-columns" class="code-input">${esc(JSON.stringify(s.profile.tracking_columns,null,2))}</textarea></label></div><details><summary class="muted">지원 필드 보기</summary><p>recipient 수취인 / phone 연락처 / address 주소 / product 품명 / quantity 총수량 / message 메시지 / channel_account 판매처_사업자 / sender_name 송하인 / sender_address 송하인주소 / sender_phone 송하인연락처 / blank 공란 / number 주문번호 / reference 배송ID / account 사업자 / channel 판매처 / carrier 택배사 / tracking 송장번호</p></details></div><div class="settings-save"><span class="small-muted">저장 위치: ${esc(state.data_path)}</span><button class="primary" type="submit">기준정보 저장</button></div></section><p class="form-error" role="alert"></p></form>`;}
function render(){
 $('#workspace-banner').hidden=workspace!=='demo';$('#workspace-label').textContent=workspace==='demo'?'가상 데이터':'실제 업무';$('#workspace-toggle').textContent='실제 업무용 웹앱 ↗';$('#workspace-toggle').dataset.action=workspace==='demo'?'live':'demo';
 $('#page-title').textContent=titles[view][0];$('#page-description').textContent=titles[view][1];$('#breadcrumb').textContent=titles[view][0];
 $$('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.view===view));$('#nav-order-count').textContent=state.orders.length;$('#nav-hold-count').textContent=state.tracking_rows.filter(r=>r.status!=='linked').length;
 $('#content').innerHTML=({dashboard,orders:ordersPage,batches:batchesPage,tracking:trackingPage,files:filesPage,settings:settingsPage}[view])();
}
async function showOrder(id){
 const o=state.orders.find(o=>o.id===id);if(!o)return;activeOrder=id;
 const detail=await api('/api/orders/'+id);const first=o.lines[0]||{};
 $('#order-detail').innerHTML=`<div class="dialog-heading"><div><div class="eyebrow">ORDER DETAIL</div><h2>${esc(o.number)} ${badge(o)}</h2><p>${esc(o.account)} · ${esc(o.channel)} · 현재 버전 ${o.revision}</p></div><button class="icon-button" data-close="order-dialog" aria-label="닫기">×</button></div><div class="detail-panel"><div class="steps-detail">${steps.map((s,i)=>`<div class="${milestones(o)[i]?'on':''}">${i+1}. ${s}<small>${milestones(o)[i]?(i===0?date(o.created):'확인됨'):'대기'}</small></div>`).join('')}</div><div class="detail-summary"><div><small>출고수량</small><b>${num(o.units)}개 / ${o.lines.length}행</b></div><div><small>송장번호</small><b>${esc(o.shipment?.tracking||'미연결')}</b></div><div><small>실제 출고</small><b>${o.shipment?.shipped_at?date(o.shipment.shipped_at)+' 확인':'미확인'}</b></div></div>${o.issues.length?`<div class="issue-list">${o.issues.map(i=>'• '+esc(i)).join('<br>')}</div>`:''}<h3>다음 작업</h3><p class="muted">${esc(o.next_action)}</p><div class="detail-actions">${!o.shipment&&!o.reviewed&&!o.issues.length?`<button class="primary small-button" data-action="order-action" data-id="${o.id}" data-op="review">검수 확정</button>`:''}${o.shipment&&!o.pending_change&&!o.manual_hold?`${!o.shipment.accepted_at?`<button class="primary small-button" data-action="order-action" data-id="${o.id}" data-op="accept">외부접수 확인</button>`:''}${o.shipment.tracking&&!o.shipment.reflected_at?`<button class="ghost small-button" data-action="order-action" data-id="${o.id}" data-op="reflect">외부반영 확인</button>`:''}${o.shipment.accepted_at&&!o.shipment.shipped_at?`<button class="ghost small-button" data-action="order-action" data-id="${o.id}" data-op="ship">실제 출고 확인</button>`:''}`:''}<button class="ghost small-button" data-action="order-action" data-id="${o.id}" data-op="${o.manual_hold?'release':'hold'}">${o.manual_hold?'담당자 보류 해제':'담당자 보류'}</button>${o.pending_change?`<button class="danger small-button" data-action="order-action" data-id="${o.id}" data-op="resolve_change">변경 확인 후 새 버전</button>`:''}</div><h3>연결된 결과물</h3>${orderArtifacts(o).map(a=>`<div class="file-item"><span class="file-icon">XLSX</span><div><a href="${download(a.id)}">${esc(kindLabel[a.file.kind])} · 주문 v${a.revision} ${a.revision!==o.revision?'(이전 버전)':''}</a><div class="meta">${esc(a.file.name)} · ${date(a.file.created)}</div></div><a class="download" href="${download(a.id)}">↓</a></div>`).join('')||'<p class="muted">이 주문의 결과물은 아직 없습니다.</p>'}${!o.shipment?`<h3>주문 정보 수정</h3><form id="order-edit-form" class="detail-form" data-id="${o.id}"><div class="form-grid"><label>수취인<input name="recipient" value="${esc(first.recipient)}" required></label><label>연락처<input name="phone" value="${esc(first.phone)}" required></label><label>주소<input name="address" value="${esc(first.address)}" required></label></div><label>배송 메시지<input name="message" value="${esc(first.message)}"></label><div class="table-scroll"><table class="line-edit"><thead><tr><th>판매코드</th><th>옵션</th><th>수량</th><th>원천 주문행번호</th></tr></thead><tbody>${o.lines.map((l,i)=>`<tr>${['sku','option','quantity','line_key'].map(k=>`<td><input name="line-${i}-${k}" value="${esc(l[k])}" aria-label="${i+1}행 ${k}"></td>`).join('')}</tr>`).join('')}</tbody></table></div><label class="checkbox-label"><input name="general_override" type="checkbox" ${o.general_override?'checked':''}>가공 의심 내용을 확인했고 일반 무가공 주문임을 확인했습니다</label><label>수정 사유<input name="note" required placeholder="누락 정보 확인, 원본 대조 결과 등"></label><button class="ghost small-button" type="submit">수정 저장 · 재검수</button><p class="form-error" role="alert"></p></form>`:''}${detail.pending_payload?`<h3>변경된 원본 내용</h3><div class="notice">${detail.pending_payload.lines.map(l=>`${esc(l.sku)} / ${esc(l.option)} / ${esc(l.quantity)}개 / ${esc(l.recipient)} / ${esc(l.address)}`).join('<br>')}</div>`:''}<h3>처리 이력</h3><ul class="timeline">${detail.events.map(e=>{let d;try{d=JSON.parse(e.detail);}catch{d={};}const text=e.action==='수정 전 데이터'?'수정 전 주문 스냅샷 보관':Object.entries(d).map(([k,v])=>k+': '+(typeof v==='object'?JSON.stringify(v):v)).join(' · ');return `<li><strong>${esc(e.action)}</strong><span class="time">${date(e.created)}</span><p>${esc(text)}</p></li>`;}).join('')}</ul></div>`;
 if(!$('#order-dialog').open)$('#order-dialog').showModal();
}
function actionDialog(context,title,description,extra=''){
 actionContext=context;$('#action-form').reset();$('#action-title').textContent=title;$('#action-description').textContent=description;$('#action-extra').innerHTML=extra;$('#action-dialog .form-error').textContent='';$('#action-dialog').showModal();
}
function requireOperator(){const name=$('#operator').value.trim();if(!name){$('#operator').focus();throw new Error('오른쪽 위 작업자 이름을 입력하세요.');}return name;}
async function fileData(file){if(!file)throw new Error('파일을 선택하세요.');if(file.size>8*1024*1024)throw new Error('8MB 이하 파일을 사용하세요.');return await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,file:reader.result.split(',')[1]});reader.onerror=reject;reader.readAsDataURL(file);});}
function captureSettings(){
 if(!$('#settings-form'))return;
 $$('[data-map]').forEach(el=>{const key=el.dataset.field;settingsDraft.mappings[Number(el.dataset.map)][key]=el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value;});
 $$('[data-sender]').forEach(el=>settingsDraft.senders[Number(el.dataset.sender)][el.dataset.field]=el.value);
 settingsDraft.profile.carrier=$('#profile-carrier').value;settingsDraft.profile.validated=$('#profile-validated').checked;settingsDraft.profile.validation_note=$('#profile-note').value;
 settingsDraft.profile.shipping_columns=JSON.parse($('#shipping-columns').value);settingsDraft.profile.tracking_columns=JSON.parse($('#tracking-columns').value);
}

document.addEventListener('click',async e=>{
 const close=e.target.closest('[data-close]');if(close){$('#'+close.dataset.close).close();return;}
 const nav=e.target.closest('[data-view]');if(nav){if(view==='settings')try{captureSettings();}catch{}view=nav.dataset.view;render();return;}
 const button=e.target.closest('[data-action]');if(!button||busy)return;
 const {action,id,op,index}=button.dataset;
 try{
 if(action==='upload'){const form=$('#upload-form');form.reset();form.elements.day.value=new Date().toLocaleDateString('en-CA');if(workspace==='demo')form.elements.account.value='데모사업자';$('#upload-dialog .form-error').textContent='';$('#upload-dialog').showModal();}
 else if(action==='refresh'){await refresh();toast('최신 상태를 확인했습니다.');}
 else if(action==='demo'){workspace='demo';await api('/api/demo',{});localStorage.setItem('chungmu.pages.workspace',workspace);filter={q:'',batch:'',status:'all'};settingsDraft=null;view='dashboard';await refresh();toast('가상 데이터 공간입니다. 실제 업무 데이터는 그대로 유지됩니다.');}
 else if(action==='live'){workspace='live';localStorage.setItem('chungmu.pages.workspace',workspace);filter={q:'',batch:'',status:'all'};settingsDraft=null;view='dashboard';await refresh();}
 else if(action==='detail')await showOrder(id);
 else if(action==='batch-orders'){filter.batch=id;view='orders';render();}
 else if(action==='generate'){
  const orders=state.orders.filter(o=>o.batch_ids.includes(id));
  actionDialog({type:'generate',id},'출고 결과물 생성',`생성 준비 ${orders.filter(o=>o.stage===1&&!isHold(o)).length}건 · 보류/미검수 ${orders.filter(o=>!o.shipment&&(isHold(o)||!o.reviewed)).length}건 · 기존 생성 ${orders.filter(o=>o.shipment).length}건입니다. 생성 대상과 제외 대상을 확인하세요.`);
 }
 else if(action==='review-batch'){const orders=state.orders.filter(o=>o.batch_ids.includes(id)&&!o.shipment&&!o.reviewed&&!o.issues.length);actionDialog({type:'review-batch',id},'정상 주문 일괄 검수',`오류 없는 미검수 ${orders.length}건을 확정합니다. 재고·품목·수량·배송정보를 모두 확인하세요. 오류·보류·이미 확정한 주문은 제외합니다.`);}
 else if(action==='export-tracking')actionDialog({type:'export-tracking',id},'송장 반영파일 생성','이 배치에 연결된 유효한 송장만 포함합니다. 외부 업로드와 송신은 별도로 수행하세요.');
 else if(action==='tracking-upload'){
  if(!state.batches.length)throw new Error('주문 배치를 먼저 입력하세요.');$('#tracking-form').reset();$('#tracking-form select').innerHTML=batchOptions(false);if(id)$('#tracking-form select').value=id;$('#tracking-dialog .form-error').textContent='';$('#tracking-dialog').showModal();
 }
 else if(action==='order-action'){
  const names={review:'검수 확정',hold:'담당자 보류',release:'보류 해제',accept:'외부접수 확인',reflect:'외부반영 확인',ship:'실제 출고 확인',resolve_change:'변경 확인 후 새 버전'};
  const desc={review:'품목·수량·배송지·재고와 출고 가능 여부를 확인한 뒤 확정하세요.',hold:'이 주문의 후속 생성을 보류합니다. 이미 접수한 외부 배송은 직접 확인하세요.',release:'담당자 보류 사유를 해소하고 재검수합니다.',accept:'택배 시스템에서 접수된 사실과 담당자 확인 근거를 남깁니다.',reflect:'반영용 파일을 업로드한 뒤 실제 송신 성공을 확인했을 때 기록하세요.',ship:'송장 생성과 별도로 물품이 실제 출고된 사실을 확인해 기록하세요.',resolve_change:'기존 생성물과 배송 이력은 보관하고 새 버전으로 재검수합니다. 기존 외부 접수 취소·수정 완료를 먼저 확인하세요.'};
  actionDialog({type:'order',id,op},names[op],desc[op],op==='resolve_change'?'<label class="checkbox-label"><input id="external-resolved" type="checkbox" required>기존 외부 접수 취소·수정 조치를 완료했습니다</label>':'');
 }
 else if(action==='link'){
  const row=state.tracking_rows.find(r=>r.id===id);const orders=state.orders.filter(o=>o.batch_ids.includes(row.batch_id)&&o.shipment&&!isHold(o));
  actionDialog({type:'link',id},'송장 직접 연결','원본 주문과 수취 정보·외부 접수 내용을 대조하고 연결 근거를 기록하세요.',`<div class="notice">송장 ${esc(row.payload.tracking)}<br>결과파일 수취인: ${esc(row.payload.recipient||'미기재')} · ${esc(row.payload.phone||'연락처 미기재')}<br>${esc(row.payload.address||'주소 미기재 · 원본 결과파일로 직접 대조')}</div><label>대상 주문 / 배송<select id="link-shipment" required><option value="">주문을 선택하세요</option>${orders.map(o=>`<option value="${o.shipment.id}">${esc(o.number)} · ${esc(o.lines[0]?.recipient)} · ${o.shipment.id}</option>`).join('')}</select></label>`);
 }
 else if(action==='backup'){requireOperator();busy=true;const r=await api('/api/backup',{});toast('체험 데이터 백업을 다운로드합니다.');if(r.download){const a=document.createElement('a');a.href=r.download;a.download=r.name;document.body.appendChild(a);a.click();a.remove();}}
 else if(['add-mapping','remove-mapping','add-sender','remove-sender'].includes(action)){
  captureSettings();if(action==='add-mapping')settingsDraft.mappings.push({account:'',channel:'*',sku:'',option:'',internal_code:'',product:'',color:'',multiplier:1,max_units:20,processing:false});
  if(action==='remove-mapping')settingsDraft.mappings.splice(Number(index),1);
  if(action==='add-sender')settingsDraft.senders.push({account:'',name:'',phone:'',address:''});
  if(action==='remove-sender')settingsDraft.senders.splice(Number(index),1);render();
 }
 }catch(err){toast(err.message,true);}finally{busy=false;}
});
document.addEventListener('input',e=>{if(e.target.id==='order-search'){filter.q=e.target.value;const start=e.target.selectionStart;render();const el=$('#order-search');el.focus();el.setSelectionRange(start,start);}});
document.addEventListener('change',e=>{if(e.target.id==='batch-filter'){filter.batch=e.target.value;render();}if(e.target.id==='status-filter'){filter.status=e.target.value;render();}});
document.addEventListener('submit',async e=>{
 const form=e.target;if(!['upload-form','tracking-form','action-form','settings-form','order-edit-form'].includes(form.id))return;e.preventDefault();if(busy)return;
 const error=form.querySelector('.form-error');if(error)error.textContent='';
 try{
  const operator=requireOperator();busy=true;form.classList.add('loading');let result;
  if(form.id==='upload-form'){
   const picked=form.elements.file.files[0];
   if(picked&&!/\.xlsx$/i.test(picked.name)){
    // Image/text/CSV: extract rows, review on screen, then the same order workflow (extract.js).
    const job=openExtraction(picked,{account:form.elements.account.value.trim(),day:form.elements.day.value,period:form.elements.period.value});
    $('#upload-dialog').close();job.catch(err=>toast(err.message,true));return;
   }
   result=await api('/api/import-orders',{...await fileData(form.elements.file.files[0]),account:form.elements.account.value,day:form.elements.day.value,period:form.elements.period.value});
   $('#upload-dialog').close();filter={q:'',batch:result.batch_id,status:'all'};view='orders';toast(result.duplicate?'동일 파일의 기존 배치를 열었습니다.':`${result.orders}개 주문을 입력했습니다.`);
  }else if(form.id==='tracking-form'){
   result=await api('/api/import-tracking',{...await fileData(form.elements.file.files[0]),batch_id:form.elements.batch_id.value});$('#tracking-dialog').close();view='tracking';filter.batch=form.elements.batch_id.value;toast(result.duplicate?'이미 입력한 송장파일입니다.':`연결 ${result.linked}건 · 직접 확인 ${result.unresolved}건`);
  }else if(form.id==='settings-form'){
   captureSettings();await api('/api/settings',settingsDraft);settingsDraft=null;toast('기준정보를 저장했습니다. 생성 전 주문은 재검수하세요.');
  }else if(form.id==='order-edit-form'){
   const o=state.orders.find(o=>o.id===form.dataset.id);let body={id:o.id,action:'edit',note:operator+' / '+form.elements.note.value,general_override:form.elements.general_override.checked,lines:o.lines.map((l,i)=>Object.fromEntries(['sku','option','quantity','line_key'].map(k=>[k,form.elements[`line-${i}-${k}`].value])))};
   ['recipient','phone','address','message'].forEach(k=>body[k]=form.elements[k].value);await api('/api/order',body);toast('수정했습니다. 다시 검수 확정하세요.');
  }else{
   const c=actionContext,note=operator+' / '+form.elements.note.value;
   if(c.type==='generate'){result=await api('/api/generate',{batch_id:c.id,note});toast(result.reused?result.message:`${result.generated}개 주문의 결과물 4종을 생성했습니다. 보류/미검수 ${result.holds}건.`);}
   if(c.type==='review-batch'){result=await api('/api/review-batch',{batch_id:c.id,note});toast(`${result.reviewed}건 검수 확정 · 기존 확정/보류 등 ${result.excluded}건 제외`);}
   if(c.type==='export-tracking'){result=await api('/api/export-tracking',{batch_id:c.id,note});toast(result.reused?'같은 송장 결과의 기존 파일을 사용합니다.':'송장 반영용 파일을 생성했습니다.');}
   if(c.type==='order'){await api('/api/order',{id:c.id,action:c.op,note,external_resolved:$('#external-resolved')?.checked||false});toast('주문 이력에 기록했습니다.');}
   if(c.type==='link'){await api('/api/link-tracking',{row_id:c.id,shipment_id:$('#link-shipment').value,note});toast('송장을 연결했습니다.');}
   $('#action-dialog').close();
  }
  await refresh();if($('#order-dialog').open&&activeOrder)await showOrder(activeOrder);
 }catch(err){if(error)error.textContent=err.message;toast(err.message,true);}finally{busy=false;form.classList.remove('loading');}
});
async function init(){try{if(workspace==='demo')await api('/api/demo',{});await refresh();}catch(e){$('#content').innerHTML=`<div class="empty"><strong>데이터를 불러오지 못했습니다</strong>${esc(e.message)}<br>서버가 실행 중인지 확인하고 새로고침하세요.</div>`;}}
init();

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
