import io
from flask import Blueprint, request, jsonify, Response, send_file, render_template
from image_lab.validation import Invalid
from .catalog import Catalog
from .maps import render, encode, seams, repeat, histogram, weave_info
from .delivery import bundle, import_bundle


def install(app,root):
    catalog=Catalog(root)
    app.extensions['loomlab']=catalog
    api=Blueprint('loomlab',__name__,url_prefix='/loom')

    def body():
        d=request.get_json(silent=True)
        if not isinstance(d,dict):
            raise Invalid('请提交JSON对象')
        return d

    def output(p,channel):
        maps=render(p,catalog.source(p))
        if channel not in maps:
            raise Invalid('未知材质通道')
        return maps[channel]

    @app.get('/')
    def home():
        return render_template('loom.html')

    @api.get('/health')
    def health():
        return jsonify({'product':'LoomLab','status':'ok','materials':len(catalog.list()),'limits':{'size':1024,'source_pixels':16777216,'weave_grid':64,'weave_colors':16,'yarns_per_direction':256}})

    @api.get('/materials')
    def listing():
        return jsonify(catalog.list())

    @api.post('/materials')
    def create():
        return jsonify(catalog.create(body().get('name','新织物'))),201

    @api.post('/demo')
    def demo():
        p=catalog.create('合成示范 · 赤陶斜纹')
        p=catalog.update(p['id'],p['revision'],{'settings':{'weave':'twill','repeat_x':16,'repeat_y':16,'warp':'#a04c35','weft':'#e1c79d'}})
        return jsonify(p),201

    @api.get('/materials/<key>')
    def read(key):
        return jsonify(catalog.read(key))

    @api.patch('/materials/<key>')
    def update(key):
        d=body()
        return jsonify(catalog.update(key,d['revision'],d))

    @api.post('/materials/<key>/source')
    def upload(key):
        file=request.files.get('file')
        if file is None:
            raise Invalid('请选择源图')
        return jsonify(catalog.upload(key,int(request.form['revision']),file.read()))

    @api.get('/materials/<key>/map/<channel>.png')
    def map_image(key,channel):
        picture=output(catalog.read(key),channel)
        if request.args.get('repeat')=='1':
            picture=repeat(picture)
        return Response(encode(picture),mimetype='image/png',headers={'Cache-Control':'no-store'})

    @api.get('/materials/<key>/inspect/<channel>')
    def inspect(key,channel):
        picture=output(catalog.read(key),channel)
        return jsonify({'channel':channel,'size':list(picture.size),'seams':seams(picture),'histogram':histogram(picture)})

    @api.get('/materials/<key>/weave-info')
    def info(key):
        return jsonify(weave_info(catalog.read(key)))

    @api.post('/materials/<key>/snapshots')
    def snapshot(key):
        d=body()
        return jsonify(catalog.snapshot(key,d['revision'],d.get('label','材质版本')))

    @api.post('/materials/<key>/snapshots/<sid>/restore')
    def restore(key,sid):
        return jsonify(catalog.restore(key,body()['revision'],sid))

    @api.get('/materials/<key>/export.zip')
    def export(key):
        return send_file(io.BytesIO(bundle(catalog,catalog.read(key))),download_name='loomlab-maps.zip',as_attachment=True,mimetype='application/zip')

    @api.get('/materials/<key>/project.zip')
    def archive(key):
        return send_file(io.BytesIO(bundle(catalog,catalog.read(key),True)),download_name='loomlab-project.zip',as_attachment=True,mimetype='application/zip')

    @api.post('/import')
    def importing():
        file=request.files.get('file')
        if file is None:
            raise Invalid('请选择工程ZIP')
        return jsonify(import_bundle(catalog,file.read())),201

    app.register_blueprint(api)
