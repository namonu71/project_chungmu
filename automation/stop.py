import json
import re
import urllib.error
import urllib.request


def main():
    base='http://127.0.0.1:8765'
    try:
        with urllib.request.urlopen(base,timeout=3) as response:
            html=response.read().decode('utf-8')
        if '충무 | 주문 운영 대시보드' not in html:
            raise RuntimeError('해당 포트의 프로그램이 충무 앱이 아닙니다. 종료하지 않았습니다.')
        token=re.search(r'name="local-token" content="([^"]+)"',html)
        if not token:raise RuntimeError('실행 중인 앱을 확인하지 못했습니다.')
        req=urllib.request.Request(base+'/api/stop',data=b'{}',headers={'Content-Type':'application/json','X-Local-Token':token.group(1)},method='POST')
        with urllib.request.urlopen(req,timeout=3) as response:
            if json.load(response).get('ok'):print('충무 업무 자동화 서버를 종료했습니다.')
    except urllib.error.URLError:
        print('실행 중인 충무 업무 자동화 서버가 없습니다.')


if __name__=='__main__':main()
