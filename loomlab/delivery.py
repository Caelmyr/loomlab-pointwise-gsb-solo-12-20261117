import io
import json
import zipfile
import re
from image_lab.validation import Invalid
from .maps import encode, render, settings, decode


def bundle(catalog,p,editable=False):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        if editable:
            z.writestr('material.json',json.dumps(p,ensure_ascii=False,indent=2))
            assets={p['source']}|{v['source'] for v in p['snapshots']}
            for asset in sorted(a for a in assets if a):
                z.writestr(asset,(catalog.directory(p['id'])/asset).read_bytes())
        else:
            maps=render(p,catalog.source(p))
            for name,picture in maps.items():
                if name=='preview' or name in ('height_raw','illumination','height_delight'):
                    continue
                z.writestr(name+'.png',encode(picture))
            z.writestr('material.json',json.dumps({'format':'loomlab-pbr-1','name':p['name'],
                'revision':p['revision'],'size':p['settings']['size'],'normal_y':p['settings']['normal_y'],
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
            if p['mode'] not in ('weave','source') or not isinstance(p['snapshots'],list) or len(p['snapshots'])>30:
                raise Invalid('工程材质结构无效')
            sources={p.get('source')}
            for snap in p['snapshots']:
                settings(snap['settings'])
                if snap['mode'] not in ('weave','source'):
                    raise Invalid('版本模式无效')
                sources.add(snap.get('source'))
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
        for name,data in assets.items():
            (catalog.directory(restored['id'])/name).write_bytes(data)
        catalog.write(restored)
        return restored
