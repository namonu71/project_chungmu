"""Local, file-based order operations. No remote system writes."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sqlite3
import uuid
import zipfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill


class UserError(Exception):
    pass


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def uid(prefix):
    return prefix + uuid.uuid4().hex[:12]


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def norm(value):
    return re.sub(r"[\s★♠◈()（）_\-]", "", clean(value).lower())


ALIASES = {
    "account": ["사업자", "계정", "ID", "3ID", "쇼핑몰ID"],
    "channel": ["판매처", "쇼핑몰", "2쇼핑몰", "쇼핑몰명"],
    "number": ["주문번호", "사방넷주문번호", "원주문번호", "고객주문번호"],
    "line_key": ["주문행번호", "주문행ID", "상세주문번호", "주문상세번호"],
    "sku": ["상품코드", "품번코드", "사방넷재고코드", "1사방넷재고코드", "재고코드"],
    "product": ["상품명", "품목명", "수건명"],
    "option": ["옵션", "옵션명", "실주문옵션명", "색상"],
    "quantity": ["수량", "주문수량"],
    "recipient": ["수취인", "수취인명", "받는분", "받는분이름", "이름"],
    "phone": ["연락처", "폰", "휴대폰", "수취인전화1", "받는분전화번호", "수취인휴대폰", "전화"],
    "address": ["주소", "배송지", "배송주소", "받는분주소"],
    "message": ["메시지", "메세지", "배송메시지", "배송메세지", "배송요청사항"],
    "source_status": ["주문상태", "상태"],
    "reference": ["배송ID", "접수참조키", "배송참조키"],
    "tracking": ["송장번호", "운송장번호", "운송장"],
    "carrier": ["택배사", "배송업체"],
}
# The original order-export worksheet includes instruction annotations in headers.
ALIASES["sku"] += ["1사방넷\n재고코드(품번코드)\n♠4번파일 만들때\n필터-색기준정렬"]
ALIASES["channel"] += ["2쇼핑몰\n◈◈공구-쇼핑몰(셀러명)\n으로 변경"]
ALIASES["product"] += ["상품명\n♠♠4번파일 복붙시 '핸드워시' 류 제외"]
LOOKUP = {norm(a): key for key, vals in ALIASES.items() for a in vals}


def read_xlsx(blob, kind="orders"):
    if len(blob) > 20 * 1024 * 1024:
        raise UserError("파일은 20MB 이하의 XLSX를 사용하세요.")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            if sum(e.file_size for e in archive.infolist()) > 100 * 1024 * 1024:
                raise UserError("압축 해제 크기가 너무 큽니다. 필요한 시트만 저장해 주세요.")
            if any("vbaProject" in n for n in archive.namelist()):
                raise UserError("매크로 파일은 지원하지 않습니다.")
        workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=False)
    except UserError:
        raise
    except Exception as exc:
        raise UserError("XLSX 파일을 읽지 못했습니다. 암호·구형 XLS는 지원하지 않습니다.") from exc
    matches = []
    required = {"number", "sku", "quantity", "recipient", "phone", "address"} if kind == "orders" else {"tracking"}
    try:
        for sheet in workbook:
            sheet_matches=[]
            for index, row in enumerate(sheet.iter_rows(max_row=10, max_col=100, values_only=True), 1):
                mapped = {}
                for col, value in enumerate(row):
                    field = LOOKUP.get(norm(value))
                    if field and field not in mapped:
                        mapped[field] = col
                if required <= mapped.keys():
                    sheet_matches.append((sheet.title, index, mapped))
                    if kind=="orders":break
            if sheet_matches:
                # Carrier exports can have grouped headings followed by a detailed
                # header row. Prefer the richer row, preserving recipient fields.
                matches.append(max(sheet_matches,key=lambda item:len(item[2])))
        # Source workbooks have one explicitly named export tab; never import a summary tab.
        if len(matches) > 1:
            preferred = [m for m in matches if m[0] in ("사방넷주문서", "주문", "주문입력", "송장결과")]
            matches = preferred if len(preferred) == 1 else matches
        if len(matches) != 1:
            raise UserError("지원 헤더 시트를 찾을 수 없거나 여러 개입니다. 입력 양식을 내려받아 한 시트로 저장하세요.")
        name, header, mapped = matches[0]
        result = []
        for index, row in enumerate(workbook[name].iter_rows(min_row=header+1, max_col=100, values_only=True), header+1):
            if index > 15010:
                raise UserError("한 파일은 15,000행 이하로 나눠 주세요.")
            record = {field: clean(row[col]) if col < len(row) else "" for field, col in mapped.items()}
            if not any(record.values()):
                continue
            if any(value.startswith("=") for value in record.values()):
                raise UserError(f"{name} {index}행에 수식이 있습니다. 원본 값을 붙여넣어 저장하세요.")
            for field in ["number","line_key","reference","tracking"]:
                if field in mapped:
                    raw=row[mapped[field]]
                    if isinstance(raw,(int,float)) and len(clean(raw).replace('.',''))>15:
                        raise UserError(f"{index}행 {field} 식별자가 긴 숫자로 저장돼 있습니다. 원천에서 텍스트 형식으로 다시 받으세요.")
            record["_row"] = index
            result.append(record)
        if not result:
            raise UserError("주문 데이터가 없습니다. 헤더 아래에 내용을 넣어 주세요.")
        return result, {"sheet": name, "header_row": header, "columns": list(mapped)}
    finally:
        workbook.close()


def workbook_bytes(headers, rows, title="목록"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title[:31]
    sheet.append(headers)
    for row in rows:
        safe = []
        for value in row:
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe.append(value)
        sheet.append(safe)
    for cell in sheet[1]:
        cell.font = Font(name="맑은 고딕", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="203750")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 28
    for index, col in enumerate(sheet.columns):
        width = max([len(clean(headers[index]))*1.6] + [min(len(clean(c.value))*1.5, 45) for c in list(col)[1:80]])
        sheet.column_dimensions[col[0].column_letter].width = max(14, min(width+3, 48))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


DEFAULT_PROFILE = {
    "carrier": "CJ대한통운", "validated": False, "validation_note": "",
    "shipping_columns": [
        ["수취인", "recipient"], ["휴대폰", "phone"], ["전화", "phone"],
        ["주소", "address"], ["상품명", "product"], ["수량", "quantity"],
        ["배송메시지", "message"], ["쇼핑몰_사업자", "channel_account"],
        ["송하인", "sender_name"], ["송하인주소", "sender_address"],
        ["송하인휴대폰", "sender_phone"], ["송하인전화", "sender_phone"],
        ["공란", "blank"], ["사방넷주문번호", "number"],
    ],
    "tracking_columns": [["ID", "account"], ["쇼핑몰", "channel"], ["사방넷주문번호", "number"], ["택배사", "carrier"], ["송장번호", "tracking"]],
}
EXPORT_FIELDS = {"recipient", "phone", "address", "product", "quantity", "message", "channel_account", "sender_name", "sender_address", "sender_phone", "blank", "number", "reference", "account", "channel", "carrier", "tracking"}


ATTACHMENT_TYPES={"image/jpeg":".jpg","image/png":".png","image/webp":".webp","text/plain":".txt","text/csv":".csv"}


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root/"originals").mkdir(exist_ok=True)
        (self.root/"artifacts").mkdir(exist_ok=True)
        (self.root/"attachments").mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, name TEXT, account TEXT, day TEXT, period TEXT, hash TEXT, created TEXT, metadata TEXT, UNIQUE(account,hash));
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, account TEXT, channel TEXT, number TEXT, payload TEXT, revision INTEGER DEFAULT 1, reviewed INTEGER DEFAULT 0, review_note TEXT DEFAULT '', manual_hold TEXT DEFAULT '', general_override INTEGER DEFAULT 0, pending TEXT, created TEXT, updated TEXT, UNIQUE(account,channel,number));
            CREATE TABLE IF NOT EXISTS batch_orders(batch_id TEXT, order_id TEXT, PRIMARY KEY(batch_id,order_id));
            CREATE TABLE IF NOT EXISTS shipments(id TEXT PRIMARY KEY, order_id TEXT, revision INTEGER, batch_id TEXT, carrier TEXT, payload TEXT, profile TEXT, active INTEGER DEFAULT 1, accepted_at TEXT, tracking TEXT, linked_at TEXT, reflected_at TEXT, shipped_at TEXT, UNIQUE(order_id,revision));
            CREATE UNIQUE INDEX IF NOT EXISTS active_tracking ON shipments(carrier,tracking) WHERE tracking IS NOT NULL AND active=1;
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,batch_id TEXT,kind TEXT,name TEXT,path TEXT,created TEXT, metadata TEXT);
            CREATE TABLE IF NOT EXISTS artifact_orders(artifact_id TEXT,order_id TEXT,revision INTEGER,PRIMARY KEY(artifact_id,order_id));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id TEXT,batch_id TEXT,action TEXT,detail TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,batch_id TEXT,name TEXT,created TEXT,hash TEXT);
            CREATE TABLE IF NOT EXISTS tracking_rows(id TEXT PRIMARY KEY,import_id TEXT,payload TEXT,status TEXT,issue TEXT,shipment_id TEXT);
            CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,batch_id TEXT,order_id TEXT,name TEXT,mime TEXT,size INTEGER,note TEXT,created TEXT);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES('profile',?)", (dumps(DEFAULT_PROFILE),))
            db.execute("INSERT OR IGNORE INTO settings VALUES('mappings','[]')")
            db.execute("INSERT OR IGNORE INTO settings VALUES('senders','[]')")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root/"operations.sqlite3", timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self, db=None):
        if db is None:
            with self.db() as conn:
                return self.settings(conn)
        return {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")}

    def event(self, db, order_id, batch_id, action, detail):
        db.execute("INSERT INTO events(order_id,batch_id,action,detail,created) VALUES(?,?,?,?,?)", (order_id,batch_id,action,dumps(detail),now()))

    def save_settings(self, data):
        if set(data) != {"profile", "mappings", "senders"}:
            raise UserError("profile·mappings·senders가 필요합니다. 기준정보 구조를 확인하세요.")
        profile=data["profile"]
        if not profile.get("carrier") or not isinstance(profile.get("validated"), bool):
            raise UserError("택배사와 검증 여부를 확인하세요.")
        if profile["validated"] and not clean(profile.get("validation_note")):
            raise UserError("실제 접수 검증일·담당자·결과를 검증 근거에 기록하세요.")
        for key in ["shipping_columns", "tracking_columns"]:
            cols=profile.get(key)
            if not isinstance(cols,list) or not 1 <= len(cols) <= 60:
                raise UserError("출력 열은 1~60개로 지정하세요.")
            for item in cols:
                if not isinstance(item,list) or len(item)!=2 or not clean(item[0]) or item[1] not in EXPORT_FIELDS:
                    raise UserError("출력 열은 [헤더, 지원 필드] 목록이어야 합니다.")
            required = {"recipient", "phone", "address", "product", "quantity", "sender_name", "sender_address", "sender_phone"} if key=="shipping_columns" else {"number", "tracking", "carrier"}
            if not required <= {c[1] for c in cols}:
                raise UserError("출력 양식에서 필수 배송·주문·송장 필드가 빠졌습니다.")
        if not {"number","reference"} & {c[1] for c in profile["shipping_columns"]}:
            raise UserError("택배 양식에 주문번호 또는 배송 참조키가 필요합니다.")
        seen=set()
        for m in data["mappings"]:
            if not all(clean(m.get(k)) for k in ["account","sku","internal_code","product","color"]):
                raise UserError("상품 매핑의 사업자·상품코드·내부코드·품명·색상을 채워 주세요.")
            key=(m["account"],m.get("channel","*"),m["sku"],m.get("option",""))
            if key in seen: raise UserError("같은 상품·옵션 매핑이 중복됐습니다.")
            seen.add(key)
            for k in ["multiplier","max_units"]:
                if not isinstance(m.get(k),int) or isinstance(m[k],bool) or not 1 <= m[k] <= 100000:
                    raise UserError("구성 수량과 한 박스 최대 수량은 양의 정수여야 합니다.")
        seen=set()
        for s in data["senders"]:
            if not all(clean(s.get(k)) for k in ["account","name","phone","address"]):
                raise UserError("송하인 사업자·이름·연락처·주소를 모두 입력하세요.")
            if s["account"] in seen: raise UserError("송하인 사업자가 중복됐습니다.")
            seen.add(s["account"])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for key,value in data.items():
                db.execute("UPDATE settings SET value=? WHERE key=?",(dumps(value),key))
            # A rule change invalidates unexported reviews; exported snapshots stay immutable.
            db.execute("UPDATE orders SET reviewed=0 WHERE NOT EXISTS(SELECT 1 FROM shipments s WHERE s.order_id=orders.id AND s.revision=orders.revision AND s.active=1)")
            self.event(db,None,None,"기준정보 변경",{"상품수":len(data["mappings"]),"택배사":profile["carrier"]})
        return {"ok":True}

    def import_orders(self, blob, name, account, day, period, digest=None):
        if not clean(account) or len(account)>100: raise UserError("사업자/계정을 입력하세요.")
        try: datetime.strptime(day,"%Y-%m-%d")
        except ValueError: raise UserError("기준일을 확인하세요.")
        if period not in ["오전","오후","추가"]: raise UserError("시간대를 선택하세요.")
        digest=digest or hashlib.sha256(blob).hexdigest()
        rows,meta=read_xlsx(blob)
        grouped=defaultdict(list)
        for row in rows:
            if row.get("account") and row["account"] != account:
                raise UserError(f"{row['_row']}행 사업자가 선택한 사업자와 다릅니다. 사업자별로 분리하세요.")
            if not row.get("number") or not row.get("channel"):
                raise UserError(f"{row['_row']}행 주문번호·판매처가 없습니다. 원본을 수정하세요.")
            row["account"]=account
            grouped[(row["channel"],row["number"])].append(row)
        bid=uid("B-")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT id FROM batches WHERE account=? AND hash=?",(account,digest)).fetchone()
            if old: return {"batch_id":old["id"],"duplicate":True,"message":"이미 입력한 파일입니다. 기존 배치를 열었습니다."}
            db.execute("INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)",(bid,Path(name).name,account,day,period,digest,now(),dumps(meta)))
            for (channel,number),lines in grouped.items():
                payload={"lines":lines}
                old=db.execute("SELECT * FROM orders WHERE account=? AND channel=? AND number=?",(account,channel,number)).fetchone()
                if old:
                    oid=old["id"]
                    def fingerprint(p):
                        return sorted(dumps({k:v for k,v in line.items() if k!="_row"}) for line in p["lines"])
                    if fingerprint(payload)!=fingerprint(json.loads(old["payload"])):
                        ship=db.execute("SELECT id FROM shipments WHERE order_id=? AND revision=? AND active=1",(oid,old["revision"])).fetchone()
                        if ship:
                            db.execute("UPDATE orders SET pending=?,updated=? WHERE id=?",(dumps(payload),now(),oid))
                            self.event(db,oid,bid,"생성 후 변경 감지",{"조치":"기존 접수 확인 필요"})
                        else:
                            db.execute("UPDATE orders SET payload=?,revision=revision+1,reviewed=0,general_override=0,updated=? WHERE id=?",(dumps(payload),now(),oid))
                            self.event(db,oid,bid,"원본 변경 반영",{"조치":"재검수"})
                    else:
                        self.event(db,oid,bid,"중첩 주문 확인",{"조치":"기존 주문 연결・중복 출고 없음"})
                else:
                    oid=uid("O-")
                    db.execute("INSERT INTO orders(id,account,channel,number,payload,created,updated) VALUES(?,?,?,?,?,?,?)",(oid,account,channel,number,dumps(payload),now(),now()))
                    self.event(db,oid,bid,"주문 입력",{"파일":Path(name).name,"원본행":[l["_row"] for l in lines]})
                db.execute("INSERT OR IGNORE INTO batch_orders VALUES(?,?)",(bid,oid))
            (self.root/"originals"/(bid+".xlsx")).write_bytes(blob)
        return {"batch_id":bid,"duplicate":False,"orders":len(grouped)}

    def import_extracted(self, headers, rows, name, account, day, period, source=None):
        """Orders transcribed from an image/text/CSV and reviewed on screen.

        The reviewed rows become an ordinary order workbook, so the same header
        recognition, validation, duplicate and revision rules as XLSX apply.
        """
        if not isinstance(headers,list) or not 1<=len(headers)<=40 or not all(isinstance(h,str) and 0<len(h)<=40 for h in headers):
            raise UserError("추출 결과의 열 구성을 확인하세요.")
        if not isinstance(rows,list) or not 1<=len(rows)<=3000:
            raise UserError("추출된 주문 행은 1~3,000행이어야 합니다.")
        table=[]
        for index,row in enumerate(rows,1):
            if not isinstance(row,list) or len(row)!=len(headers):raise UserError(f"{index}번째 행의 열 수가 맞지 않습니다.")
            cells=[clean(v) for v in row]
            if any(len(v)>500 for v in cells):raise UserError(f"{index}번째 행에 500자를 넘는 값이 있습니다.")
            if any(cells):table.append(cells)
        if not table:raise UserError("입력할 주문 행이 없습니다.")
        mime=extension=None;data=b""
        if source:
            mime=clean(source.get("mime"))
            extension=ATTACHMENT_TYPES.get(mime)
            if not extension:raise UserError("원본 자료는 이미지(JPG·PNG·WEBP), TXT, CSV만 지원합니다.")
            try:data=base64.b64decode(source.get("file",""),validate=True)
            except Exception as exc:raise UserError("원본 자료를 읽지 못했습니다.") from exc
            if not 0<len(data)<=10*1024*1024:raise UserError("원본 자료는 10MB 이하만 보관할 수 있습니다.")
        # Same reviewed content means the same input, regardless of workbook timestamps.
        digest="extracted:"+hashlib.sha256(dumps([headers,table]).encode("utf-8")).hexdigest()
        result=self.import_orders(workbook_bytes(headers,table,"주문입력"),name,account,day,period,digest)
        if result["duplicate"] or not source:return result
        aid=uid("A-");bid=result["batch_id"];source_name=Path(clean(source.get("name")) or "원본자료").name[:180]
        with self.db() as db:
            (self.root/"attachments"/(aid+extension)).write_bytes(data)
            db.execute("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?)",(aid,bid,None,source_name,mime,len(data),"주문 추출 원본",now()))
            meta=json.loads(db.execute("SELECT metadata FROM batches WHERE id=?",(bid,)).fetchone()["metadata"])
            meta.update({"extracted":True,"source":{"id":aid,"name":source_name,"mime":mime}})
            db.execute("UPDATE batches SET metadata=? WHERE id=?",(dumps(meta),bid))
            self.event(db,None,bid,"원본 자료 보관",{"파일":source_name,"유형":mime})
        return result

    def attachment_file(self,aid):
        with self.db() as db:
            row=db.execute("SELECT * FROM attachments WHERE id=?",(aid,)).fetchone()
        if not row:raise UserError("파일이 없습니다.")
        path=(self.root/"attachments"/(row["id"]+ATTACHMENT_TYPES.get(row["mime"],""))).resolve()
        if not path.is_relative_to(self.root/"attachments") or not path.exists():raise UserError("파일을 찾을 수 없습니다. 백업을 확인하세요.")
        return path,row["name"],row["mime"]

    def evaluate(self, order, settings, shipment=None):
        order=dict(order)
        payload=json.loads(order["payload"])
        issues=[]; lines=[]; total=0
        source=payload["lines"]
        if order["pending"]: issues.append("생성 후 원본 변경: 기존 접수 취소·수정 확인 필요")
        if order["manual_hold"]: issues.append("담당자 보류: "+order["manual_hold"])
        if not source: issues.append("주문행 없음")
        addresses=set(); keys=[]
        processing=False; cancelled=False
        for raw in source:
            line=dict(raw); line_issues=[]
            for field,label in [("recipient","수취인"),("phone","연락처"),("address","주소")]:
                if not clean(raw.get(field)): line_issues.append(label+" 누락")
            phone=re.sub(r"\D","",raw.get("phone",""))
            if raw.get("phone") and not 8 <= len(phone) <= 15: line_issues.append("연락처 형식 확인")
            addresses.add(tuple(raw.get(k,"") for k in ["recipient","phone","address"]))
            quantity=raw.get("quantity","")
            if not re.fullmatch(r"[1-9]\d*",quantity) or len(quantity)>7:
                q=0;line_issues.append("수량은 양의 정수여야 함")
            else:q=int(quantity)
            candidates=[m for m in settings["mappings"] if m["account"]==order["account"] and m["sku"]==raw.get("sku") and m.get("option","")==raw.get("option","") and m.get("channel","*") in ["*",order["channel"]]]
            exact=[m for m in candidates if m.get("channel","*")==order["channel"]]
            candidates=exact or candidates
            mapping=candidates[0] if len(candidates)==1 else None
            if not mapping:line_issues.append("미매핑 상품·옵션")
            units=q*(mapping["multiplier"] if mapping else 1)
            total+=units
            combined=" ".join(raw.get(k,"") for k in ["product","option","message"])
            if re.search(r"자수|전사|포토|라벨|고리.?제거|스티커",combined) or (mapping and mapping.get("processing")):processing=True
            if re.search(r"변경|취소|분할|합포장|나눠|따로.?배송|박스.?\d|\d.?박스",raw.get("message","")):line_issues.append("배송 메시지의 변경·포장 요청 확인")
            if re.search(r"취소|환불",raw.get("source_status","")):cancelled=True
            stable=raw.get("line_key") or "|".join([raw.get("sku",""),raw.get("option","")])
            keys.append(stable)
            line.update({"quantity_value":q,"units":units,"mapping":mapping,"issues":line_issues})
            lines.append(line)
            issues.extend(line_issues)
        if len(addresses)>1:issues.append("한 주문의 수취 정보가 서로 다름")
        if len(keys)!=len(set(keys)):issues.append("반복 주문행 식별자: 원천 주문행번호 확인")
        if processing and not order["general_override"]:issues.append("가공 의심 주문: 일반 출고 여부 확인")
        if cancelled:issues.append("취소·환불 주문: 별도 확인")
        max_units=min([l["mapping"]["max_units"] for l in lines if l["mapping"]] or [0])
        if max_units and total>max_units:issues.append("한 박스 최대 수량 초과: 복수 박스 수동 처리")
        sender=next((s for s in settings["senders"] if s["account"]==order["account"]),None)
        if not sender:issues.append("사업자 송하인 정보 미등록")
        issues=list(dict.fromkeys(issues))
        if shipment:
            snapshot=json.loads(shipment["payload"]).get("order_snapshot")
            if snapshot:
                # A generated order always shows its actual shipped-data version,
                # even when master mappings subsequently change.
                lines=snapshot["lines"];total=snapshot["units"];sender=snapshot["sender"]
                issues=[]
                if order["pending"]:issues.append("생성 후 원본 변경: 기존 접수 취소·수정 확인 필요")
                if order["manual_hold"]:issues.append("담당자 보류: "+order["manual_hold"])
        stage=0;label="검수 대기";next_action="주문 검수 후 확정"
        if issues:label="보류";next_action=issues[0]
        elif order["reviewed"]:stage=1;label="생성 준비";next_action="배치 결과물 생성"
        if shipment:
            shipment=dict(shipment)
            stage=2;label="파일 생성";next_action="택배 접수 후 외부접수 확인"
            if shipment["accepted_at"]:stage=3;label="외부접수 확인";next_action="송장 결과파일 업로드"
            if shipment["tracking"]:stage=4;label="송장 연결";next_action="송장 반영용 파일 생성·업로드"
            if shipment["reflected_at"]:stage=5;label="외부반영 확인";next_action="실제 출고 확인"
            if shipment["shipped_at"] and shipment["reflected_at"]:label="처리 완료";next_action="완료"
            if order["pending"] or order["manual_hold"]:label="변경·보류";next_action=issues[0] if issues else "보류 확인"
        return {**{k:v for k,v in order.items() if k not in ["payload","pending"]},"lines":lines,"issues":issues,"units":total,"sender":sender,"stage":stage,"status":label,"next_action":next_action,"pending_change":bool(order["pending"]),"shipment":shipment}

    def update_order(self, oid, data):
        note=clean(data.get("note"))
        if not note:raise UserError("확인자 또는 처리 사유를 입력하세요.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
            if not row:raise UserError("주문을 찾을 수 없습니다.")
            ship=db.execute("SELECT * FROM shipments WHERE order_id=? AND revision=? AND active=1",(oid,row["revision"])).fetchone()
            action=data.get("action")
            if action=="resolve_change":
                if not row["pending"]:raise UserError("대기 중인 변경이 없습니다.")
                if not data.get("external_resolved"):raise UserError("기존 외부 접수 취소·수정 완료 여부를 확인하세요.")
                db.execute("UPDATE shipments SET active=0 WHERE order_id=?",(oid,))
                db.execute("UPDATE orders SET payload=pending,pending=NULL,revision=revision+1,reviewed=0,general_override=0,manual_hold='',updated=? WHERE id=?",(now(),oid))
            elif action in ["accept","reflect","ship"]:
                if not ship or row["pending"] or row["manual_hold"]:raise UserError("유효한 배송이 없거나 변경·보류 중입니다.")
                if action=="reflect" and not ship["tracking"]:raise UserError("송장 연결을 먼저 완료하세요.")
                if action=="reflect" and not ship["accepted_at"]:raise UserError("외부접수 확인을 먼저 기록하세요.")
                if action=="reflect" and not db.execute("SELECT 1 FROM artifact_orders ao JOIN artifacts a ON a.id=ao.artifact_id WHERE ao.order_id=? AND ao.revision=? AND a.kind='tracking'",(oid,row["revision"])).fetchone():raise UserError("송장 반영용 파일을 먼저 생성하세요.")
                if action=="ship" and not ship["accepted_at"]:raise UserError("외부접수 확인을 먼저 기록하세요.")
                column={"accept":"accepted_at","reflect":"reflected_at","ship":"shipped_at"}[action]
                db.execute(f"UPDATE shipments SET {column}=? WHERE id=?",(now(),ship["id"]))
            elif action=="hold":
                db.execute("UPDATE orders SET manual_hold=?,reviewed=0,updated=? WHERE id=?",(note,now(),oid))
            elif action=="release":
                db.execute("UPDATE orders SET manual_hold='',reviewed=0,updated=? WHERE id=?",(now(),oid))
            elif action in ["review","edit"]:
                if ship:raise UserError("파일 생성 후에는 직접 수정할 수 없습니다. 변경된 원본 파일을 다시 입력하세요.")
                if action=="edit":
                    payload=json.loads(row["payload"])
                    for line in payload["lines"]:
                        for k in ["recipient","phone","address","message"]:
                            if k in data:line[k]=clean(data[k])
                    if "lines" in data:
                        if len(data["lines"])!=len(payload["lines"]):raise UserError("행 수는 변경할 수 없습니다.")
                        for line,update in zip(payload["lines"],data["lines"]):
                            for k in ["sku","option","quantity","line_key"]:
                                if k in update:line[k]=clean(update[k])
                    self.event(db,oid,None,"수정 전 데이터",json.loads(row["payload"]))
                    db.execute("UPDATE orders SET payload=?,reviewed=0,general_override=?,updated=? WHERE id=?",(dumps(payload),int(bool(data.get("general_override"))),now(),oid))
                else:
                    view=self.evaluate(row,self.settings(db))
                    if view["issues"]:raise UserError("보류 사유를 먼저 해결하세요: "+" / ".join(view["issues"]))
                    db.execute("UPDATE orders SET reviewed=1,review_note=?,updated=? WHERE id=?",(note,now(),oid))
            else:raise UserError("지원하지 않는 작업입니다.")
            self.event(db,oid,None,{"review":"검수 확정","edit":"주문 수정","hold":"담당자 보류","release":"보류 해제","accept":"외부접수 확인","reflect":"외부반영 확인","ship":"실제 출고 확인","resolve_change":"변경 해소・새 버전"}.get(action,action),{"확인":note})
        return {"ok":True}

    def _artifact(self,db,bid,kind,headers,rows,orders,metadata=None):
        aid=uid("F-")
        label={"review":"출고검수","summary":"품목별출고집계","shipping":"택배등록","hold":"보류목록","tracking":"송장반영"}[kind]
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        verification="검증용_" if kind in ["shipping","tracking"] and not (metadata or {}).get("validated") else ""
        name=f"{verification}{bid}_{label}_{stamp}_{aid[-4:]}.xlsx"
        path=self.root/"artifacts"/(aid+".xlsx")
        path.write_bytes(workbook_bytes(headers,rows,label))
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)",(aid,bid,kind,name,str(path),now(),dumps(metadata or {})))
        for oid,revision in orders:
            db.execute("INSERT INTO artifact_orders VALUES(?,?,?)",(aid,oid,revision))
        return aid

    def review_batch(self,bid,note):
        if not clean(note):raise UserError("검수 담당자와 재고·품목·배송정보 확인 내용을 입력하세요.")
        count=0;excluded=0
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            settings=self.settings(db)
            if not db.execute("SELECT 1 FROM batches WHERE id=?",(bid,)).fetchone():raise UserError("배치를 찾을 수 없습니다.")
            orders=list(db.execute("SELECT o.* FROM orders o JOIN batch_orders b ON b.order_id=o.id WHERE b.batch_id=?",(bid,)))
            for order in orders:
                ship=db.execute("SELECT * FROM shipments WHERE order_id=? AND revision=? AND active=1",(order["id"],order["revision"])).fetchone()
                if ship or order["reviewed"] or self.evaluate(order,settings,ship)["issues"]:
                    excluded+=1
                    continue
                db.execute("UPDATE orders SET reviewed=1,review_note=?,updated=? WHERE id=?",(note,now(),order["id"]))
                self.event(db,order["id"],bid,"일괄 검수 확정",{"확인":note})
                count+=1
        return {"reviewed":count,"excluded":excluded}

    def generate(self,bid,note):
        if not clean(note):raise UserError("생성 담당자와 대상·제외 주문 확인 내용을 입력하세요.")
        created=[]
        try:
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                batch=db.execute("SELECT * FROM batches WHERE id=?",(bid,)).fetchone()
                if not batch:raise UserError("배치가 없습니다.")
                settings=self.settings(db);profile=settings["profile"]
                orders=list(db.execute("SELECT o.* FROM orders o JOIN batch_orders b ON b.order_id=o.id WHERE b.batch_id=?",(bid,)))
                ready=[];holds=[];excluded=0
                for row in orders:
                    ship=db.execute("SELECT * FROM shipments WHERE order_id=? AND revision=? AND active=1",(row["id"],row["revision"])).fetchone()
                    view=self.evaluate(row,settings,ship)
                    if ship:excluded+=1;continue
                    if view["issues"] or not row["reviewed"]:holds.append(view)
                    else:ready.append(view)
                if not ready:
                    existing=[r["id"] for r in db.execute("SELECT id FROM artifacts WHERE batch_id=? AND kind IN ('review','summary','shipping','hold')",(bid,))]
                    if existing:return {"artifacts":existing,"reused":True,"message":"새 생성 대상이 없습니다. 기존 결과물을 사용하세요."}
                    raise UserError("검수 확정된 새 주문이 없습니다.")
                pairs=[(o["id"],o["revision"]) for o in ready]
                review=[];shipping=[];summary=defaultdict(int)
                for order in ready:
                    sid=uid("S-")
                    first=order["lines"][0];sender=order["sender"]
                    values={**first,"quantity":order["units"],"reference":sid,"number":order["number"],"account":order["account"],"channel":order["channel"],"channel_account":order["channel"]+"_"+order["account"],"product":" / ".join(f"{l['mapping']['product']} {l['mapping']['color']} {l['units']}개" for l in order["lines"]),"sender_name":sender["name"],"sender_phone":sender["phone"],"sender_address":sender["address"],"blank":"","carrier":profile["carrier"]}
                    values["order_snapshot"]={"lines":order["lines"],"units":order["units"],"sender":sender}
                    db.execute("INSERT INTO shipments(id,order_id,revision,batch_id,carrier,payload,profile) VALUES(?,?,?,?,?,?,?)",(sid,order["id"],order["revision"],bid,profile["carrier"],dumps(values),dumps(profile)))
                    for line in order["lines"]:
                        m=line["mapping"]
                        review.append([order["number"],order["account"],order["channel"],sid,line["sku"],m["internal_code"],m["product"],m["color"],line["quantity_value"],line["units"],first["recipient"],first["phone"],first["address"],first.get("message",""),order["revision"]])
                        summary[(m["internal_code"],m["product"],m["color"])]+=line["units"]
                    shipping.append([values.get(field,"") for _,field in profile["shipping_columns"]])
                    self.event(db,order["id"],bid,"출고파일 생성",{"배송ID":sid,"확인":note,"규칙해시":hashlib.sha256(dumps(settings).encode()).hexdigest()})
                metadata={"validated":profile["validated"],"profile":profile,"rules_snapshot":settings,"settings_hash":hashlib.sha256(dumps(settings).encode()).hexdigest(),"generated_orders":len(ready),"excluded_existing":excluded,"hold_count":len(holds),"note":note}
                created.append(self._artifact(db,bid,"review",["주문번호","사업자","판매처","배송ID","판매상품코드","내부품목코드","품목명","색상","주문수량","출고수량","수취인","연락처","주소","메시지","버전"],review,pairs,metadata))
                created.append(self._artifact(db,bid,"summary",["내부품목코드","품목명","색상","출고수량"],[list(k)+[v] for k,v in sorted(summary.items())],pairs,metadata))
                created.append(self._artifact(db,bid,"shipping",[h for h,_ in profile["shipping_columns"]],shipping,pairs,metadata))
                created.append(self._artifact(db,bid,"hold",["주문번호","사업자","판매처","보류사유","버전"],[[o["number"],o["account"],o["channel"]," / ".join(o["issues"]) or "검수 미확정",o["revision"]] for o in holds],[(o["id"],o["revision"]) for o in holds],metadata))
            return {"artifacts":created,"generated":len(ready),"holds":len(holds),"reused":False}
        except Exception:
            for aid in created:(self.root/"artifacts"/(aid+".xlsx")).unlink(missing_ok=True)
            raise

    def import_tracking(self,blob,name,bid):
        rows,meta=read_xlsx(blob,"tracking")
        digest=hashlib.sha256(blob).hexdigest();iid=uid("I-")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM batches WHERE id=?",(bid,)).fetchone():raise UserError("배치를 선택하세요.")
            old=db.execute("SELECT id FROM imports WHERE batch_id=? AND hash=?",(bid,digest)).fetchone()
            if old:return {"import_id":old["id"],"duplicate":True}
            db.execute("INSERT INTO imports VALUES(?,?,?,?,?)",(iid,bid,Path(name).name,now(),digest))
            shipments=[dict(s) for s in db.execute("SELECT s.*,o.number,o.account,o.channel,o.pending,o.manual_hold FROM shipments s JOIN orders o ON o.id=s.order_id JOIN batch_orders b ON b.order_id=o.id WHERE b.batch_id=? AND s.active=1",(bid,))]
            candidates=[]
            for row in rows:
                track=re.sub(r"[\s-]","",row.get("tracking",""))
                row["tracking"]=track
                matches=[]
                if row.get("reference"):matches=[s for s in shipments if s["id"]==row["reference"]]
                elif row.get("number"):matches=[s for s in shipments if s["number"]==row["number"]]
                if row.get("reference") and row.get("number"):
                    matches=[s for s in matches if s["number"]==row["number"]]
                for key in ["account","channel","carrier"]:
                    if row.get(key):matches=[s for s in matches if s[key]==row[key]]
                issue=""
                if not re.fullmatch(r"[0-9]{8,20}",track):issue="송장번호 형식 확인"
                elif len(matches)!=1:issue="유일한 주문 식별자가 없습니다. 직접 연결하세요."
                elif matches[0]["pending"] or matches[0]["manual_hold"]:issue="변경·보류 주문은 연결할 수 없습니다."
                candidates.append((row,matches,issue))
            track_counts=Counter(r[0]["tracking"] for r in candidates)
            ship_counts=Counter(r[1][0]["id"] for r in candidates if len(r[1])==1)
            linked=0
            for row,matches,issue in candidates:
                sid=matches[0]["id"] if len(matches)==1 else None
                if track_counts[row["tracking"]]>1:issue="파일 안에 중복 송장번호가 있습니다."
                if sid and ship_counts[sid]>1:issue="한 배송에 여러 송장행이 있습니다."
                rid=uid("T-")
                db.execute("INSERT INTO tracking_rows VALUES(?,?,?,?,?,?)",(rid,iid,dumps(row),"unresolved",issue,None))
                if not issue and sid:
                    try:
                        self._link(db,rid,sid,"식별자 자동 대조")
                        linked+=1
                    except UserError as exc:
                        db.execute("UPDATE tracking_rows SET issue=? WHERE id=?",(str(exc),rid))
            (self.root/"originals"/(iid+".xlsx")).write_bytes(blob)
            return {"import_id":iid,"linked":linked,"unresolved":len(rows)-linked,"duplicate":False}

    def _link(self,db,rid,sid,note):
        row=db.execute("SELECT t.*,i.batch_id FROM tracking_rows t JOIN imports i ON i.id=t.import_id WHERE t.id=?",(rid,)).fetchone()
        ship=db.execute("SELECT s.*,o.pending,o.manual_hold FROM shipments s JOIN orders o ON o.id=s.order_id WHERE s.id=? AND s.active=1",(sid,)).fetchone()
        if not row or not ship:raise UserError("송장행 또는 배송을 찾을 수 없습니다.")
        if row["status"]=="linked":raise UserError("이미 연결한 송장행입니다.")
        if not db.execute("SELECT 1 FROM batch_orders WHERE batch_id=? AND order_id=?",(row["batch_id"],ship["order_id"])).fetchone():raise UserError("다른 배치의 배송입니다.")
        if ship["pending"] or ship["manual_hold"]:raise UserError("변경·보류 주문은 연결할 수 없습니다.")
        payload=json.loads(row["payload"]);tracking=payload["tracking"]
        if payload.get("carrier") and payload["carrier"]!=ship["carrier"]:raise UserError("송장 결과의 택배사가 배송과 다릅니다.")
        if not re.fullmatch(r"\d{8,20}",tracking):raise UserError("송장번호는 숫자 8~20자리여야 합니다.")
        if ship["tracking"] and ship["tracking"]!=tracking:raise UserError("기존 송장이 있습니다. 자동으로 덮어쓰지 않습니다.")
        conflict=db.execute("SELECT id FROM shipments WHERE carrier=? AND tracking=? AND active=1 AND id!=?",(ship["carrier"],tracking,sid)).fetchone()
        if conflict:raise UserError("다른 주문에 이미 사용한 송장번호입니다.")
        # Same file duplicates cannot be manually bypassed into a second shipment.
        others=list(db.execute("SELECT id,payload,status FROM tracking_rows WHERE import_id=? AND id!=?",(row["import_id"],rid)))
        if any(json.loads(o["payload"]).get("tracking")==tracking for o in others):raise UserError("파일 내 중복 송장을 정리해 다시 입력하세요.")
        db.execute("UPDATE shipments SET tracking=?,linked_at=? WHERE id=?",(tracking,now(),sid))
        db.execute("UPDATE tracking_rows SET status='linked',issue='',shipment_id=? WHERE id=?",(sid,rid))
        self.event(db,ship["order_id"],row["batch_id"],"송장 연결",{"송장번호":tracking,"근거":note,"결과행":rid})

    def link_tracking(self,rid,sid,note):
        if not clean(note):raise UserError("연결 확인자와 대조 근거를 입력하세요.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._link(db,rid,sid,note)
        return {"ok":True}

    def export_tracking(self,bid,note):
        if not clean(note):raise UserError("생성 확인자를 입력하세요.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            ships=list(db.execute("SELECT s.*,o.pending,o.manual_hold FROM shipments s JOIN orders o ON o.id=s.order_id JOIN batch_orders b ON b.order_id=o.id WHERE b.batch_id=? AND s.active=1 AND s.tracking IS NOT NULL",(bid,)))
            ships=[s for s in ships if not s["pending"] and not s["manual_hold"]]
            if not ships:raise UserError("송장 연결이 완료된 유효한 주문이 없습니다.")
            # All output adapters are pinned at shipment generation time.
            profiles={dumps(json.loads(s["profile"])) for s in ships}
            if len(profiles)!=1:raise UserError("출력 양식 버전이 섞여 있습니다. 생성 배치별로 나눠 주세요.")
            profile=json.loads(next(iter(profiles)))
            fingerprint=hashlib.sha256(dumps(sorted((s["id"],s["tracking"]) for s in ships)).encode()).hexdigest()
            for artifact in db.execute("SELECT * FROM artifacts WHERE batch_id=? AND kind='tracking'",(bid,)):
                if json.loads(artifact["metadata"]).get("fingerprint")==fingerprint:return {"artifact_id":artifact["id"],"reused":True}
            rows=[];pairs=[]
            for s in ships:
                values={**json.loads(s["payload"]),"tracking":s["tracking"],"carrier":s["carrier"]}
                rows.append([values.get(field,"") for _,field in profile["tracking_columns"]])
                pairs.append((s["order_id"],s["revision"]))
            aid=self._artifact(db,bid,"tracking",[h for h,_ in profile["tracking_columns"]],rows,pairs,{"validated":profile["validated"],"profile":profile,"fingerprint":fingerprint,"note":note})
            for s in ships:self.event(db,s["order_id"],bid,"송장 반영파일 생성",{"파일ID":aid,"확인":note})
            return {"artifact_id":aid,"reused":False}

    def dashboard(self):
        with self.db() as db:
            settings=self.settings(db)
            batches=[dict(r) for r in db.execute("SELECT * FROM batches ORDER BY created DESC,rowid DESC")]
            links=defaultdict(list)
            for r in db.execute("SELECT * FROM batch_orders"):links[r["order_id"]].append(r["batch_id"])
            ships={r["order_id"]:r for r in db.execute("SELECT * FROM shipments WHERE active=1")}
            artifacts=[dict(r) for r in db.execute("SELECT id,batch_id,kind,name,created,metadata FROM artifacts ORDER BY created DESC,rowid DESC")]
            arts=defaultdict(list)
            for r in db.execute("SELECT * FROM artifact_orders"):
                arts[r["order_id"]].append({"id":r["artifact_id"],"revision":r["revision"]})
            orders=[]
            tracking_ids={a["id"] for a in artifacts if a["kind"]=="tracking"}
            for row in db.execute("SELECT * FROM orders ORDER BY updated DESC,rowid DESC"):
                view=self.evaluate(row,settings,ships.get(row["id"]))
                view["batch_ids"]=links[row["id"]]
                view["artifacts"]=arts[row["id"]]
                view["tracking_exported"]=any(a["id"] in tracking_ids and a["revision"]==row["revision"] for a in arts[row["id"]])
                if view["shipment"] and not view["pending_change"] and not view["manual_hold"]:
                    ship=view["shipment"]
                    if ship["tracking"] and not ship["accepted_at"]:view["next_action"]="외부접수 확인 기록"
                    elif ship["tracking"] and not ship["reflected_at"]:view["next_action"]="업로드·송신 후 외부반영 확인" if view["tracking_exported"] else "송장 반영파일 생성"
                # Raw shipment snapshots contain addresses; the local detail view needs them.
                if view["shipment"]:
                    view["shipment"]={k:v for k,v in view["shipment"].items() if k not in ["payload","profile"]}
                orders.append(view)
            tracking=[]
            for row in db.execute("SELECT t.*,i.batch_id,i.name FROM tracking_rows t JOIN imports i ON i.id=t.import_id ORDER BY i.created DESC,t.rowid"):
                r=dict(row);r["payload"]=json.loads(r["payload"]);tracking.append(r)
            events=[dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 500")]
            for a in artifacts:a["metadata"]=json.loads(a["metadata"])
            for b in batches:b["metadata"]=json.loads(b["metadata"])
            return {"batches":batches,"orders":orders,"artifacts":artifacts,"tracking_rows":tracking,"events":events,"settings":settings,"data_path":str(self.root)}

    def detail(self,oid):
        with self.db() as db:
            row=db.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
            if not row:raise UserError("주문을 찾을 수 없습니다.")
            return {"events":[dict(r) for r in db.execute("SELECT * FROM events WHERE order_id=? ORDER BY id DESC",(oid,))],"pending_payload":json.loads(row["pending"]) if row["pending"] else None}

    def artifact_file(self,aid):
        with self.db() as db:
            row=db.execute("SELECT * FROM artifacts WHERE id=?",(aid,)).fetchone()
            if not row:raise UserError("파일이 없습니다.")
            path=Path(row["path"]).resolve()
            if not path.is_relative_to(self.root/"artifacts") or not path.exists():raise UserError("파일을 찾을 수 없습니다. 백업을 확인하세요.")
            return path,row["name"]

    def backup(self):
        """Consistent DB snapshot plus original and generated files, invoked locally."""
        folder=self.root/"backups";folder.mkdir(exist_ok=True)
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:4]
        target=folder/("충무업무백업_"+stamp+".zip")
        snapshot=folder/("snapshot_"+stamp+".sqlite3")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Lock writers during both the database and its matching file snapshot.
            source=sqlite3.connect(self.root/"operations.sqlite3")
            dest=sqlite3.connect(snapshot)
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()
            with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as z:
                z.write(snapshot,"operations.sqlite3")
                for directory in ["originals","artifacts"]:
                    for file in (self.root/directory).glob("*.xlsx"):z.write(file,file.relative_to(self.root).as_posix())
                for file in (self.root/"attachments").glob("A-*"):
                    if file.suffix in ATTACHMENT_TYPES.values():z.write(file,file.relative_to(self.root).as_posix())
        snapshot.unlink(missing_ok=True)
        return target
