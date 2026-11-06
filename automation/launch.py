"""Double-click entry point. Reuses an already running local instance."""
import json
import sys
import urllib.request
import webbrowser
from .server import main


def run():
    url='http://127.0.0.1:8765'
    try:
        with urllib.request.urlopen(url+'/api/state',timeout=2) as response:
            data=json.load(response)
        if all(key in data for key in ['batches','orders','settings','data_path']):
            webbrowser.open(url)
            print('이미 실행 중인 충무 업무 자동화 화면을 열었습니다.')
            return
        raise RuntimeError('다른 프로그램이 8765 포트를 사용 중입니다.')
    except (OSError,ValueError):
        pass
    import threading
    # The browser request can wait for the server to enter its serve loop.
    threading.Timer(1,lambda:webbrowser.open(url)).start()
    main()


if __name__=='__main__':run()
