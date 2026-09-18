import getpass,uuid,httpx
class Client:
    def __init__(self,url,email,origin=None):
        self.url=url.rstrip('/');self.http=httpx.Client(base_url=self.url+'/api/v1',timeout=60,headers={'Origin':origin or self.url})
        r=self.http.post('/auth/login',json={'email':email,'password':getpass.getpass('Password: ')});r.raise_for_status();self.http.headers['X-CSRF-Token']=r.json()['data']['csrf_token']
    def get(self,path):
        r=self.http.get(path);r.raise_for_status();return r.json()['data']
    def post(self,path,body=None,files=None,data=None):
        r=self.http.post(path,json=body,files=files,data=data,headers={'Idempotency-Key':str(uuid.uuid4())});r.raise_for_status();return r.json()['data']
