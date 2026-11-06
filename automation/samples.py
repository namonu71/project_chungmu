"""Artificial fixtures and downloadable input formats. Contains no customer data."""
from .core import workbook_bytes, dumps

ORDER_HEADERS=["사업자","판매처","주문번호","주문행번호","상품코드","상품명","옵션","수량","수취인","연락처","주소","메시지","주문상태"]


def demo_settings():
    from .core import DEFAULT_PROFILE
    import copy
    return {
        "profile":copy.deepcopy(DEFAULT_PROFILE),
        "mappings":[
            {"account":"데모사업자","channel":"*","sku":"DEMO-200-W","option":"화이트","internal_code":"T200-W","product":"호텔타올 200g","color":"화이트","multiplier":1,"max_units":20,"processing":False},
            {"account":"데모사업자","channel":"*","sku":"DEMO-170-G","option":"그레이","internal_code":"T170-G","product":"데일리타올 170g","color":"그레이","multiplier":1,"max_units":25,"processing":False},
        ],
        "senders":[{"account":"데모사업자","name":"충무 데모","phone":"01000000000","address":"대전광역시 데모구 샘플로 1"}],
    }


def demo_orders():
    rows=[]
    for i in range(1,13):
        sku="DEMO-200-W" if i%2 else "DEMO-170-G"
        option="화이트" if i%2 else "그레이"
        rows.append(["데모사업자","스마트스토어",f"DEMO-{i:04d}",f"L-{i}",sku,"호텔타올" if i%2 else "데일리타올",option,2+i%4,f"샘플고객{i:02d}","01000000000",f"대전광역시 데모구 샘플로 {i}","","정상"])
    rows[8][4]="UNMAPPED-001"
    rows[9][10]=""
    rows[10][11]="자수 문구 확인 요청"
    rows[11][7]=40
    return workbook_bytes(ORDER_HEADERS,rows,"주문입력")


def template(kind):
    if kind=="orders":return workbook_bytes(ORDER_HEADERS,[],"주문입력"),"주문입력_양식.xlsx"
    if kind=="demo_orders":return demo_orders(),"샘플주문_가상데이터.xlsx"
    if kind=="tracking":return workbook_bytes(["배송ID","주문번호","판매처","택배사","송장번호"],[],"송장결과"),"송장결과_양식.xlsx"
    raise ValueError("없는 양식입니다.")


def seed_demo(store):
    if store.dashboard()["batches"]:return
    store.save_settings(demo_settings())
    bid=store.import_orders(demo_orders(),"가상주문_시연용.xlsx","데모사업자","2026-11-06","오전")["batch_id"]
    orders=sorted(store.dashboard()["orders"],key=lambda o:o["number"])
    for o in orders[:5]:store.update_order(o["id"],{"action":"review","note":"데모 담당자 / 수량·배송정보 대조"})
    store.generate(bid,"데모 담당자 / 정상 5건과 보류·미검수 주문 확인")
    state=store.dashboard();orders=sorted(state["orders"],key=lambda o:o["number"])
    for o in orders[:4]:store.update_order(o["id"],{"action":"accept","note":"가상 접수 확인 / 실제 택배 접수 아님"})
    blob=workbook_bytes(["배송ID","송장번호"],[[o["shipment"]["id"],f"99000000{i:04d}"] for i,o in enumerate(orders[:3],1)],"송장결과")
    store.import_tracking(blob,"가상송장_시연용.xlsx",bid)
    store.export_tracking(bid,"가상 송장 대조 완료")
    for o in orders[:2]:store.update_order(o["id"],{"action":"reflect","note":"가상 외부반영 확인"})
    store.update_order(orders[0]["id"],{"action":"ship","note":"가상 출고 확인"})
    for o in orders[5:7]:store.update_order(o["id"],{"action":"review","note":"가상 검수 완료"})

