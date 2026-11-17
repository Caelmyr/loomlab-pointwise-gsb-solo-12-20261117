import io
import json
import zipfile
import re
from image_lab.validation import Invalid
from .maps import encode, render, settings, decode, blend_transform, MASK_KEYS


def project_assets(p):
    assets=set()
    for v in [p]+list(p.get('snapshots',[])):
        if v.get('source'):
            assets.add(v['source'])
        blend=v.get('blend')
        if blend:
            for side in ('a','b'):
                if blend[side].get('source'):
                    assets.add(blend[side]['source'])
            if blend.get('mask'):
                assets.add(blend['mask'])
    return assets


def blend_sources_of(blend):
    if not isinstance(blend,dict):
        raise Invalid('混合结构无效')
    found=set()
    for side in ('a','b'):
        child=blend.get(side)
        if not isinstance(child,dict) or child.get('mode') not in ('weave','source'):
            raise Invalid('混合来源结构无效')
        settings(child.get('settings'))
        if child['mode']=='source' and not child.get('source'):
            raise Invalid('混合来源缺少源图')
        found.add(child.get('source'))
    mask=blend.get('mask')
    if mask is not None and not isinstance(mask,str):
        raise Invalid('遮罩引用无效')
    found.add(mask)
    blend_transform({k:blend[k] for k in MASK_KEYS if k in blend})
    return found


def bundle(catalog,p,editable=False):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        if editable:
            z.writestr('material.json',json.dumps(p,ensure_ascii=False,indent=2))
            for asset in sorted(project_assets(p)):
                z.writestr(asset,(catalog.directory(p['id'])/asset).read_bytes())
        else:
            maps=render(p,catalog.source(p),catalog.blend_images(p))
            for name,picture in maps.items():
                if name not in ('preview','a','b','mask'):
                    z.writestr(name+'.png',encode(picture))
            z.writestr('material.json',json.dumps({'format':'loomlab-pbr-1','name':p['name'],
                'revision':p['revision'],'mode':p['mode'],'size':p['settings']['size'],'normal_y':p['settings']['normal_y'],
                'color_spaces':{'albedo':'sRGB','height':'linear','normal':'linear','roughness':'linear','ao':'linear','orm':'linear'},
                'orm':{'r':'ao','g':'roughness','b':'metallic=0'},
                'height':'normalized relative height; no measured displacement unit'},ensure_ascii=False,indent=2))
    return stream.getvalue()


def import_bundle(catalog,raw):
    if len(raw)>64*1024*1024:
        raise Invalid('工程压缩包最多64MB')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            entries=z.infolist()
            if len(entries)>40 or sum(e.file_size for e in entries)>96*1024*1024:
                raise Invalid('工程解压预算超限')
            names=[e.filename for e in entries]
            if len(names)!=len(set(names)) or any('/' in n or '\\' in n for n in names):
                raise Invalid('工程文件名无效')
            p=json.loads(z.read('material.json'))
            settings(p['settings'])
            if p['mode'] not in ('weave','source','blend') or not isinstance(p['snapshots'],list) or len(p['snapshots'])>30:
                raise Invalid('工程材质结构无效')
            sources={p.get('source')}
            if p['mode']=='blend':
                sources|=blend_sources_of(p.get('blend'))
            for snap in p['snapshots']:
                settings(snap['settings'])
                if snap['mode'] not in ('weave','source','blend'):
                    raise Invalid('版本模式无效')
                sources.add(snap.get('source'))
                if snap.get('blend') is not None:
                    sources|=blend_sources_of(snap['blend'])
            assets={}
            for asset in sources-{None}:
                if not isinstance(asset,str) or not re.fullmatch('[0-9a-f]{64}\\.png',asset):
                    raise Invalid('源图文件名无效')
                assets[asset]=encode(decode(z.read(asset)))
            if p['mode']=='source' and not p.get('source'):
                raise Invalid('源图模式缺少图像')
    except Invalid:
        raise
    except Exception as error:
        raise Invalid('无法读取LoomLab工程') from error
    with catalog.lock:
        restored=catalog.create(p['name'])
        restored.update({k:p[k] for k in ('mode','settings','source','snapshots')})
        if p['mode']=='blend':
            restored['blend']=p['blend']
        for name,data in assets.items():
            (catalog.directory(restored['id'])/name).write_bytes(data)
        catalog.write(restored)
        return restored
