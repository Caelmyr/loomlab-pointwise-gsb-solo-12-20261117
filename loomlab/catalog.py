import copy
import hashlib
import json
import uuid
import threading
import time
from pathlib import Path
from image_lab.validation import Invalid, Conflict
from .maps import settings, decode, encode, blend_transform


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

    def frozen(self,src):
        return {'name':src['name'],'revision':src['revision'],'mode':src['mode'],
                'settings':copy.deepcopy(src['settings']),'source':src['source']}

    def copy_asset(self,src,asset,dest_key):
        if asset:
            data=(self.directory(src['id'])/asset).read_bytes()
            (self.directory(dest_key)/asset).write_bytes(data)

    def create_blend(self,name,a_key,b_key):
        with self.lock:
            sources=[self.read(a_key),self.read(b_key)]
            for src in sources:
                if src['mode']=='blend':
                    raise Invalid('混合材质不能再次作为来源')
            p = {'id':uuid.uuid4().hex[:24],'name':str(name)[:100],
                 'revision':0,'created':time.time(),'mode':'blend',
                 'settings':settings(),'source':None,
                 'blend':{'a':self.frozen(sources[0]),'b':self.frozen(sources[1]),
                          'mask':None,**blend_transform()},
                 'snapshots':[]}
            self.write(p)
            for side,src in zip(('a','b'),sources):
                self.copy_asset(src,p['blend'][side]['source'],p['id'])
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
                if data['mode']=='blend' or p['mode']=='blend':
                    raise Invalid('混合材质模式不可更改')
                if data['mode'] not in ('weave','source'):
                    raise Invalid('模式须为weave/source')
                if data['mode']=='source' and not p['source']:
                    raise Invalid('请先导入源图')
                p['mode']=data['mode']
            if 'blend' in data:
                if p['mode']!='blend':
                    raise Invalid('仅混合材质支持混合设置')
                self.apply_blend(p,data['blend'])
        return self.change(key,revision,apply)

    def apply_blend(self,p,data):
        if not isinstance(data,dict):
            raise Invalid('混合设置须为对象')
        data=dict(data)
        for side in ('a','b'):
            key=data.pop(side,None)
            if key is None:
                continue
            src=self.read(key)
            if src['id']==p['id']:
                raise Invalid('混合来源不能是自身')
            if src['mode']=='blend':
                raise Invalid('混合材质不能再次作为来源')
            child=self.frozen(src)
            p['blend'][side]=child
            self.copy_asset(src,child['source'],p['id'])
        if 'mask' in data:
            if data.pop('mask') is not None:
                raise Invalid('遮罩请通过上传设置')
            p['blend']['mask']=None
        transform={k:p['blend'].get(k,v) for k,v in blend_transform().items()}
        transform.update(data)
        p['blend'].update(blend_transform(transform))

    def source(self,p):
        if not p['source']:
            return None
        return decode((self.directory(p['id'])/p['source']).read_bytes())

    def blend_images(self,p):
        result={}
        blend=p.get('blend')
        if not blend:
            return result
        directory=self.directory(p['id'])
        for side in ('a','b'):
            asset=blend[side].get('source')
            if asset:
                result[side]=decode((directory/asset).read_bytes())
        if blend.get('mask'):
            result['mask']=decode((directory/blend['mask']).read_bytes())
        return result

    def upload(self,key,revision,raw):
        picture=decode(raw)
        raw=encode(picture)
        asset=hashlib.sha256(raw).hexdigest()+'.png'
        def apply(p):
            (self.directory(key)/asset).write_bytes(raw)
            p['source']=asset
            p['mode']='source'
        return self.change(key,revision,apply)

    def upload_mask(self,key,revision,raw):
        picture=decode(raw).convert('L')
        raw=encode(picture)
        asset=hashlib.sha256(raw).hexdigest()+'.png'
        def apply(p):
            if p['mode']!='blend':
                raise Invalid('仅混合材质支持遮罩')
            (self.directory(key)/asset).write_bytes(raw)
            p['blend']['mask']=asset
        return self.change(key,revision,apply)

    def snapshot(self,key,revision,label):
        def apply(p):
            if len(p['snapshots'])>=30:
                raise Invalid('最多30个材质版本')
            p['snapshots'].append({'id':uuid.uuid4().hex[:24],
                'label':str(label)[:80],'settings':copy.deepcopy(p['settings']),
                'mode':p['mode'],'source':p['source'],'blend':copy.deepcopy(p.get('blend'))})
        return self.change(key,revision,apply)

    def restore(self,key,revision,sid):
        def apply(p):
            snap=next((v for v in p['snapshots'] if v['id']==sid),None)
            if snap is None:
                raise Invalid('版本不存在')
            p['settings']=copy.deepcopy(snap['settings'])
            p['mode']=snap['mode']
            p['source']=snap['source']
            blend=snap.get('blend')
            if blend is None:
                p.pop('blend',None)
            else:
                p['blend']=copy.deepcopy(blend)
        return self.change(key,revision,apply)
