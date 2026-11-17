import io
import hashlib
import math
import numpy as np
from PIL import Image, ImageOps
from scipy.ndimage import gaussian_filter, uniform_filter
from image_lab.validation import Invalid


def encode(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def decode(raw):
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        if image.width * image.height > 4096 * 4096:
            raise Invalid('源图像最多1600万像素')
        image.load()
        return image.convert('RGBA')
    except Invalid:
        raise
    except Exception as error:
        raise Invalid('无法读取PNG/JPEG图像') from error


def number(value, lo, hi, label):
    if isinstance(value, bool):
        raise Invalid(label + '需要数值')
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise Invalid(label + '需要数值') from error
    if not math.isfinite(value) or not lo <= value <= hi:
        raise Invalid(f'{label}须为{lo}至{hi}')
    return value


def color(value):
    if not isinstance(value, str) or len(value) != 7 or value[0] != '#':
        raise Invalid('颜色须为#RRGGBB')
    try:
        return np.array([int(value[k:k+2], 16) for k in (1,3,5)],dtype=np.float32)/255
    except ValueError as error:
        raise Invalid('颜色须为#RRGGBB') from error


def settings(data=None):
    result = {'size':256,'repeat_x':12,'repeat_y':12,'weave':'plain',
              'warp':'#b06b47','weft':'#d5bd83','gap':.18,'relief':.65,
              'height_blur':.6,'height_low':0.,'height_high':1.,'invert':False,
              'delight':False,'delight_scale':16.,'delight_strength':.8,
              'normal_strength':3.,'normal_y':'up','roughness':.65,'roughness_detail':.2,
              'ao_radius':3.,'ao_strength':1.5,'source_scale':1.,'source_x':0.,'source_y':0.,
              'source_turn':0.,'light_azimuth':315.,'light_elevation':45.}
    if data:
        unknown = set(data)-set(result)
        if unknown:
            raise Invalid('不支持设置：'+', '.join(sorted(unknown)))
        result.update(data)
    for key,lo,hi in [('size',32,1024),('repeat_x',1,64),('repeat_y',1,64),
                      ('gap',.02,.8),('relief',0,1),('height_blur',0,12),
                      ('height_low',0,1),('height_high',0,1),
                      ('delight_scale',2,256),('delight_strength',0,1),
                      ('normal_strength',0,20),
                      ('roughness',0,1),('roughness_detail',0,1),('ao_radius',1,32),
                      ('ao_strength',0,8),('source_scale',.1,10),('source_x',-2,2),
                      ('source_y',-2,2),('source_turn',-360,360),
                      ('light_azimuth',0,360),('light_elevation',5,90)]:
        result[key] = number(result[key],lo,hi,key)
    for key in ('size','repeat_x','repeat_y'):
        if result[key] != int(result[key]):
            raise Invalid(key+'需要整数')
        result[key] = int(result[key])
    if result['height_low'] >= result['height_high']:
        raise Invalid('高度低点必须小于高点')
    if result['weave'] not in ('plain','twill','basket'):
        raise Invalid('组织支持plain/twill/basket')
    if result['normal_y'] not in ('up','down'):
        raise Invalid('法线Y方向支持up/down')
    result['invert'] = bool(result['invert'])
    result['delight'] = bool(result['delight'])
    color(result['warp'])
    color(result['weft'])
    return result


def image(array):
    return Image.fromarray(np.round(np.clip(array,0,1)*255).astype('uint8'))


def linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb+.055)/1.055)**2.4)


def srgb(rgb):
    return np.where(rgb <= .0031308, rgb*12.92, 1.055*np.maximum(rgb,0)**(1/2.4)-.055)


def weave(s):
    n = s['size']
    y,x = np.mgrid[:n,:n].astype(np.float32)
    u = (x+.5)/n*s['repeat_x']
    v = (y+.5)/n*s['repeat_y']
    ix,iy = np.floor(u).astype(int),np.floor(v).astype(int)
    if s['weave']=='plain':
        over = (ix+iy)%2==0
    elif s['weave']=='twill':
        over = (ix+iy)%4<2
    else:
        over = (ix//2+iy//2)%2==0
    width = 1-s['gap']
    warp = np.sqrt(np.maximum(0,1-((u%1-.5)/(.5*width))**2))
    weft = np.sqrt(np.maximum(0,1-((v%1-.5)/(.5*width))**2))
    height = np.where(over,warp*.7+weft*.15,weft*.7+warp*.15)*s['relief']
    visible_warp = np.where(over,warp>0,(weft<=0)&(warp>0))
    visible = (warp>0)|(weft>0)
    shade = .5+.5*np.where(visible_warp,warp,weft)
    base = np.where(visible_warp[:,:,None],color(s['warp']),color(s['weft']))
    base = np.where(visible[:,:,None],base*shade[:,:,None],.055)
    return base.astype(np.float32),height.astype(np.float32)


def sample_source(source,s):
    n = s['size']
    rgba = np.asarray(source,dtype=np.float32)/255
    h,w = rgba.shape[:2]
    y,x = np.mgrid[:n,:n].astype(np.float32)
    u = ((x+.5)/n-.5)/s['source_scale']
    v = ((y+.5)/n-.5)/s['source_scale']
    theta = math.radians(s['source_turn'])
    su = u*math.cos(theta)+v*math.sin(theta)+.5+s['source_x']
    sv = -u*math.sin(theta)+v*math.cos(theta)+.5+s['source_y']
    px = np.floor(su*w).astype(int)%w
    py = np.floor(sv*h).astype(int)%h
    sampled = rgba[py,px]
    return srgb(linear(sampled[:,:,:3])*sampled[:,:,3:]+linear(np.array(.055))*(1-sampled[:,:,3:]))


def grayscale(base,raw_height):
    if raw_height is None:
        rgb = linear(base)
        h = rgb[:,:,0]*.2126+rgb[:,:,1]*.7152+rgb[:,:,2]*.0722
    else:
        h = raw_height
    return np.clip(h,0,1).astype(np.float32)


def remove_illumination(h,s):
    """估计并扣除缓慢明暗变化。

    用 wrap 模式大尺度高斯滤波估计照明基线（与平铺边界一致，不产生接缝条纹），
    再以加法高通 h - strength*(基线-均值) 去除大范围光照。全程只做加减与裁剪：
    纯色或大面积近黑图像的基线与画面相同，结果保持恒定，不会出现除法导致的
    无穷值、放大噪声或突然顶满；细织纹属于高频分量，原样保留。
    """
    slow = gaussian_filter(h,float(s['delight_scale']),mode='wrap')
    corrected = h - s['delight_strength']*(slow-slow.mean())
    return slow.astype(np.float32),np.clip(corrected,0,1).astype(np.float32)


def height_levels(h,s):
    h = np.clip((h-s['height_low'])/(s['height_high']-s['height_low']),0,1)
    if s['invert']:
        h = 1-h
    if s['height_blur']:
        h = gaussian_filter(h,s['height_blur'],mode='wrap')
    return h.astype(np.float32)


def height_map(base,raw_height,s):
    h = grayscale(base,raw_height)
    if s['delight']:
        _,h = remove_illumination(h,s)
    return height_levels(h,s)


def normals(height,s):
    dx = (np.roll(height,-1,axis=1)-np.roll(height,1,axis=1))*.5
    dy = (np.roll(height,-1,axis=0)-np.roll(height,1,axis=0))*.5
    nx = -dx*s['normal_strength']
    ny = dy*s['normal_strength']
    if s['normal_y']=='down':
        ny = -ny
    nz = np.ones_like(height)
    vectors = np.stack([nx,ny,nz],axis=2)
    vectors /= np.linalg.norm(vectors,axis=2,keepdims=True)
    return vectors


def roughness(height,s):
    detail = height-uniform_filter(height,size=7,mode='wrap')
    return np.clip(s['roughness']-detail*s['roughness_detail']*3,0,1)


def occlusion(height,s):
    neighbourhood = gaussian_filter(height,s['ao_radius'],mode='wrap')
    return np.clip(1-np.maximum(neighbourhood-height,0)*s['ao_strength'],0,1)


def shade(base,normal,rough,ao,s):
    az = math.radians(s['light_azimuth'])
    el = math.radians(s['light_elevation'])
    light = np.array([math.cos(az)*math.cos(el),math.sin(az)*math.cos(el),math.sin(el)])
    geometric = normal.copy()
    if s['normal_y']=='down':
        geometric[:,:,1] *= -1
    diffuse = np.maximum(np.sum(geometric*light,axis=2),0)
    half = light+np.array([0,0,1])
    half /= np.linalg.norm(half)
    ndh = np.maximum(np.sum(geometric*half,axis=2),0)
    exponent = 2+80*(1-rough)**2
    specular = ndh**exponent*(1-rough)*.16
    lit = linear(base)*(.16+.84*diffuse[:,:,None])*ao[:,:,None]+specular[:,:,None]
    return np.clip(srgb(lit),0,1)


def render(project,source=None):
    s = settings(project['settings'])
    if project['mode']=='source' and source is not None:
        base,raw = sample_source(source,s),None
    else:
        base,raw = weave(s)
    gray = grayscale(base,raw)
    if s['delight']:
        slow,cleaned = remove_illumination(gray,s)
        height = height_levels(cleaned,s)
        diagnostics = {'height_raw':image(height_levels(gray,s)),
                       'illumination':image(slow),
                       'height_delight':image(height)}
    else:
        height = height_levels(gray,s)
        diagnostics = {}
    normal = normals(height,s)
    rough = roughness(height,s)
    ao = occlusion(height,s)
    maps = {'albedo':image(base),'height':image(height),'normal':image(normal*.5+.5),
            'roughness':image(rough),'ao':image(ao),'preview':image(shade(base,normal,rough,ao,s)),
            'orm':image(np.stack([ao,rough,np.zeros_like(ao)],axis=2))}
    maps.update(diagnostics)
    return maps


def seams(picture):
    a = np.asarray(picture.convert('RGB'),dtype=np.float32)/255
    horizontal = np.abs(a[:,0]-a[:,-1])
    vertical = np.abs(a[0]-a[-1])
    return {'left_right_mean':float(horizontal.mean()),'top_bottom_mean':float(vertical.mean()),
            'left_right_peak':float(horizontal.max()),'top_bottom_peak':float(vertical.max()),
            'note':'边缘像素差是诊断量；连续采样纹理的首尾像素无需相等'}


def repeat(picture,count=3):
    if count not in (2,3,4):
        raise Invalid('平铺次数为2/3/4')
    thumb = picture.copy()
    thumb.thumbnail((384,384))
    out = Image.new(picture.mode,(thumb.width*count,thumb.height*count))
    for row in range(count):
        for col in range(count):
            out.paste(thumb,(col*thumb.width,row*thumb.height))
    return out


def histogram(picture):
    a = np.asarray(picture.convert('L'))
    counts = np.bincount(a.ravel(),minlength=256)
    return {'counts':counts.tolist(),'min':int(a.min()),'max':int(a.max()),
            'mean':float(a.mean()),'std':float(a.std())}
