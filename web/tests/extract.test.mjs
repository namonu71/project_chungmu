import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// extract.js is a classic browser script; load its pure parsing functions in a sandbox.
const ctx=vm.createContext({TextDecoder,window:{}});
vm.runInContext(readFileSync(new URL('../public/extract.js',import.meta.url),'utf8')+';this.extractFromText=extractFromText;',ctx);
const run=(text)=>JSON.parse(JSON.stringify(ctx.extractFromText(text,{account:'데모사업자'})));

test('CSV with headers, quotes and aliases',()=>{const r=run('판매처,주문번호,상품코드,옵션,수량,받는분,전화번호,배송주소\n스마트스토어,A-1,DEMO-200-W,화이트,3개,"홍길동","010-1234-5678","대전, 샘플로 1"\n');assert.equal(r.recognized,true);assert.equal(r.rows.length,1);assert.deepEqual([r.rows[0].account,r.rows[0].number,r.rows[0].quantity,r.rows[0].recipient,r.rows[0].address],['데모사업자','A-1','3','홍길동','대전, 샘플로 1']);});
test('tab-separated and OCR space-aligned tables',()=>{assert.equal(run('주문번호\t상품코드\t수량\nB-1\tX\t2\nB-2\tY\t1').rows.length,2);const r=run('주문번호   상품코드   수량   수취인\nC-1   DEMO-170-G   4   가상고객');assert.deepEqual([r.rows[0].number,r.rows[0].sku,r.rows[0].quantity],['C-1','DEMO-170-G','4']);});
test('key-value message blocks with item lines inheriting the order',()=>{const r=run('[주문서]\n판매처: 스마트스토어\n주문번호 : D-1\n수취인: 가상고객\n연락처: 01000000000\n주소: 대전 샘플로 2\n상품코드: DEMO-200-W\n수량: 2\n상품코드: DEMO-170-G\n수량: 1\n\n주문번호: D-2\n판매처: 쿠팡\n상품코드: DEMO-200-W\n수량: 5');assert.equal(r.rows.length,3);assert.deepEqual(r.rows.map(x=>[x.number,x.sku,x.quantity,x.address]),[['D-1','DEMO-200-W','2','대전 샘플로 2'],['D-1','DEMO-170-G','1','대전 샘플로 2'],['D-2','DEMO-200-W','5','']]);});
test('unrecognized text yields no rows',()=>{const r=run('안녕하세요 수건 주문하고 싶어요');assert.equal(r.recognized,false);assert.equal(r.rows.length,0);});
test('real OCR output with paragraph breaks stays one order',()=>{const r=run('판매처: 스마트스토어\n\n주문번호: IMG-7001\n수취인: 가상고객\n\n연락처: 010-1234-5678\n주소: 대전광역시 샘플로 15\n상품코드: DEMO-200-W\n옵션: 화이트\n\n수량: 3\n');assert.equal(r.rows.length,1);assert.deepEqual([r.rows[0].channel,r.rows[0].number,r.rows[0].sku,r.rows[0].option,r.rows[0].quantity,r.rows[0].phone],['스마트스토어','IMG-7001','DEMO-200-W','화이트','3','010-1234-5678']);});
