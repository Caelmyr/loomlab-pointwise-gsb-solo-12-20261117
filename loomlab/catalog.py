import copy
import json
import re
import uuid
import threading
import time
from pathlib import Path
from image_lab.validation import Invalid, Conflict
from .maps import settings, decode, encode

ASSET_NAME = re.compile(r'[0-9a-f]{64}\.png')


class Catalog:
    def __init__(self,root):
        self.root = Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()

    def directory(self,key):
        if not isinstance(key,str) or len(key)!=24 or any(c not in '0123456789abcdef' for c in key):
            raise Invalid('无效材质ID')
        return self.root/key

    def read(self,key):
        with self.lock:
            path = self.directory(key)/'material.json'
            if not path.is_file():
                raise Invalid('材质不存在')
            return json.loads(path.read_text())

    def write(self,p):
        directory = self.directory(p['id'])
        directory.mkdir(exist_ok=True)
        temp = directory/('save-'+uuid.uuid4().hex)
        temp.write_text(json.dumps(p,ensure_ascii=False,indent=2))
        temp.replace(directory/'material.json')

    def create(self,name='新织物'):
        with self.lock:
            p = {'id':uuid.uuid4().hex[:24],'name':str(name)[:100],
                 'revision':0,'created':time.time(),'mode':'weave',
                 'settings':settings(),'source':None,'snapshots':[]}
            self.write(p)
            return p

    def list(self):
        with self.lock:
            values=[]
            for path in sorted(self.root.glob('*/material.json')):
                p=json.loads(path.read_text())
                values.append({k:p[k] for k in ('id','name','revision','mode')})
            return values

    def change(self,key,revision,fn):
        with self.lock:
            p=self.read(key)
            if int(revision)!=p['revision']:
                raise Conflict('材质已变化，请刷新后重试')
            fn(p)
            p['revision']+=1
            self.write(p)
            return copy.deepcopy(p)

    def update(self,key,revision,data):
        def apply(p):
            if 'name' in data:
                p['name']=str(data['name'])[:100]
            if 'settings' in data:
                p['settings']=settings({**p['settings'],**data['settings']})
            if 'mode' in data:
                if data['mode'] not in ('weave','source'):
                    raise Invalid('模式须为weave/source')
                if data['mode']=='source' and not p['source']:
                    raise Invalid('请先导入源图')
                p['mode']=data['mode']
        return self.change(key,revision,apply)

    def source(self,p):
        if not p['source']:
            return None
        return decode((self.directory(p['id'])/p['source']).read_bytes())

    def asset(self,key,name):
        """只读读取材质目录内的不可变源图（含历史版本引用的源图）。"""
        if not name:
            return None
        if not isinstance(name,str) or not ASSET_NAME.fullmatch(name):
            raise Invalid('源图文件名无效')
        path=self.directory(key)/name
        if not path.is_file():
            raise Invalid('版本引用的源图缺失')
        return decode(path.read_bytes())

    def upload(self,key,revision,raw):
        import hashlib
        picture=decode(raw)
        raw=encode(picture)
        asset=hashlib.sha256(raw).hexdigest()+'.png'
        def apply(p):
            (self.directory(key)/asset).write_bytes(raw)
            p['source']=asset
            p['mode']='source'
        return self.change(key,revision,apply)

    def snapshot(self,key,revision,label):
        def apply(p):
            if len(p['snapshots'])>=30:
                raise Invalid('最多30个材质版本')
            p['snapshots'].append({'id':uuid.uuid4().hex[:24],
                'label':str(label)[:80],'settings':copy.deepcopy(p['settings']),
                'mode':p['mode'],'source':p['source']})
        return self.change(key,revision,apply)

    def restore(self,key,revision,sid):
        def apply(p):
            snap=next((v for v in p['snapshots'] if v['id']==sid),None)
            if snap is None:
                raise Invalid('版本不存在')
            p['settings']=copy.deepcopy(snap['settings'])
            p['mode']=snap['mode']
            p['source']=snap['source']
        return self.change(key,revision,apply)
