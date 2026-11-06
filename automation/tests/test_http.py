import base64
import io
import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from automation.server import make_server
from automation.samples import demo_orders


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=make_server(self.temp.name,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        html=urllib.request.urlopen(self.base).read().decode()
        self.token=re.search(r'name="local-token" content="([^"]+)"',html).group(1)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,body=None,token=True,workspace='live'):
        headers={'Content-Type':'application/json','X-Workspace':workspace}
        if token:headers['X-Local-Token']=self.token
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        return urllib.request.urlopen(request)

    def test_live_demo_isolation(self):
        self.request('/api/demo',{},workspace='demo').close()
        self.assertEqual(len(json.load(self.request('/api/state',workspace='demo'))['orders']),12)
        self.assertEqual(len(json.load(self.request('/api/state'))['orders']),0)

    def test_mutations_need_local_token_and_host(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/demo',{},token=False)
        self.assertEqual(caught.exception.code,403)
        req=urllib.request.Request(self.base+'/api/state',headers={'Host':'untrusted.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code,403)

    def test_upload_and_template_download(self):
        body={'name':'sample.xlsx','file':base64.b64encode(demo_orders()).decode(),'account':'데모사업자','day':'2026-11-06','period':'오전'}
        r=json.load(self.request('/api/import-orders',body));self.assertEqual(r['orders'],12)
        response=self.request('/api/templates/orders')
        self.assertIn('spreadsheetml',response.headers['Content-Type']);self.assertTrue(response.read().startswith(b'PK'))
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/files/../../server.py')
        self.assertEqual(caught.exception.code,400)


if __name__=='__main__':unittest.main()
