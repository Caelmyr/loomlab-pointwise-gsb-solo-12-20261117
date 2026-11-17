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


def color_list(value,label):
    if not isinstance(value,list) or not 1 <= len(value) <= MAX_COLORS:
        raise Invalid(label+f'须为1至{MAX_COLORS}个颜色')
    for item in value:
        color(item)
    return [str(item) for item in value]


MAX_GRID = 64
MAX_COLORS = 16
MAX_YARNS = 256


def lcm(a,b):
    return a*b//math.gcd(int(a),int(b))


def grid_value(value):
    if not isinstance(value,list) or not 2 <= len(value) <= MAX_GRID:
        raise Invalid(f'自定义组织网格须为2至{MAX_GRID}行')
    width = None
    rows = []
    for row in value:
        if not isinstance(row,list) or not 2 <= len(row) <= MAX_GRID:
            raise Invalid(f'自定义组织网格每行须为2至{MAX_GRID}格')
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise Invalid('自定义组织网格各行长度须一致')
        cells = []
        for cell in row:
            if isinstance(cell,bool) or not isinstance(cell,int) or cell not in (0,1):
                raise Invalid('组织格值须为0（纬纱在上）或1（经纱在上）')
            cells.append(cell)
        rows.append(cells)
    return rows


def settings(data=None):
    result = {'size':256,'repeat_x':12,'repeat_y':12,'weave':'plain',
              'warp':'#b06b47','weft':'#d5bd83',
              'warp_colors':['#b06b47'],'weft_colors':['#d5bd83'],
              'custom_grid':[[1,0],[0,1]],
              'gap':.18,'relief':.65,
              'height_blur':.6,'height_low':0.,'height_high':1.,'invert':False,
              'normal_strength':3.,'normal_y':'up','roughness':.65,'roughness_detail':.2,
              'ao_radius':3.,'ao_strength':1.5,'source_scale':1.,'source_x':0.,'source_y':0.,
              'source_turn':0.,'light_azimuth':315.,'light_elevation':45.}
    if data:
        unknown = set(data)-set(result)
        if unknown:
            raise Invalid('不支持设置：'+', '.join(sorted(unknown)))
        result.update(data)
        # 兼容旧工程/旧客户端：未提供配色循环时，跟随单色字段
        if 'warp_colors' not in data:
            result['warp_colors'] = [result['warp']]
        if 'weft_colors' not in data:
            result['weft_colors'] = [result['weft']]
    for key,lo,hi in [('size',32,1024),('repeat_x',1,64),('repeat_y',1,64),
                      ('gap',.02,.8),('relief',0,1),('height_blur',0,12),
                      ('height_low',0,1),('height_high',0,1),('normal_strength',0,20),
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
    if result['weave'] not in ('plain','twill','basket','custom'):
        raise Invalid('组织支持plain/twill/basket/custom')
    if result['normal_y'] not in ('up','down'):
        raise Invalid('法线Y方向支持up/down')
    result['invert'] = bool(result['invert'])
    color(result['warp'])
    color(result['weft'])
    result['warp_colors'] = color_list(result['warp_colors'],'经纱配色循环')
    result['weft_colors'] = color_list(result['weft_colors'],'纬纱配色循环')
    # 单色字段跟随配色循环首色，保证旧通道取值仍有意义
    result['warp'] = result['warp_colors'][0]
    result['weft'] = result['weft_colors'][0]
    if result['weave'] == 'custom':
        result['custom_grid'] = grid_value(result['custom_grid'])
        px,py = weave_periods(result)
        if (lcm(px,len(result['warp_colors'])) > MAX_YARNS or
                lcm(py,len(result['weft_colors'])) > MAX_YARNS):
            raise Invalid('组织循环与配色循环的共同周期超过%d根纱，请缩小网格或配色'%MAX_YARNS)
    return result


def weave_periods(s):
    """返回组织自身的（经向循环根数，纬向循环根数）。"""
    if s['weave'] == 'custom':
        grid = s['custom_grid']
        return len(grid[0]),len(grid)
    if s['weave'] == 'twill':
        return 4,4
    if s['weave'] == 'basket':
        return 4,4
    return 2,2


def effective_repeats(s):
    """组织循环与配色循环取共同周期，并向上取整到完整周期，避免截断末根纱。"""
    px,py = weave_periods(s)
    cx = lcm(px,len(s['warp_colors']))
    cy = lcm(py,len(s['weft_colors']))
    rx = min(MAX_YARNS, math.ceil(s['repeat_x']/cx)*cx)
    ry = min(MAX_YARNS, math.ceil(s['repeat_y']/cy)*cy)
    return cx,cy,rx,ry


def image(array):
    return Image.fromarray(np.round(np.clip(array,0,1)*255).astype('uint8'))


def linear(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb+.055)/1.055)**2.4)


def srgb(rgb):
    return np.where(rgb <= .0031308, rgb*12.92, 1.055*np.maximum(rgb,0)**(1/2.4)-.055)


def weave(s):
    n = s['size']
    y,x = np.mgrid[:n,:n].astype(np.float32)
    _,_,rx,ry = effective_repeats(s)
    u = (x+.5)/n*rx
    v = (y+.5)/n*ry
    ix,iy = np.floor(u).astype(int),np.floor(v).astype(int)
    if s['weave']=='plain':
        over = (ix+iy)%2==0
    elif s['weave']=='twill':
        over = (ix+iy)%4<2
    elif s['weave']=='basket':
        over = (ix//2+iy//2)%2==0
    else:
        # 网格行=纬纱（自上而下），列=经纱（自左而右），1=经纱在上
        grid = np.array(s['custom_grid'],dtype=np.uint8)
        gh,gw = grid.shape
        over = grid[iy%gh,ix%gw].astype(bool)
    width = 1-s['gap']
    warp = np.sqrt(np.maximum(0,1-((u%1-.5)/(.5*width))**2))
    weft = np.sqrt(np.maximum(0,1-((v%1-.5)/(.5*width))**2))
    height = np.where(over,warp*.7+weft*.15,weft*.7+warp*.15)*s['relief']
    visible_warp = np.where(over,warp>0,(weft<=0)&(warp>0))
    visible = (warp>0)|(weft>0)
    shade = .5+.5*np.where(visible_warp,warp,weft)
    warp_palette = np.stack([color(c) for c in s['warp_colors']])
    weft_palette = np.stack([color(c) for c in s['weft_colors']])
    warp_rgb = warp_palette[ix%len(warp_palette)]
    weft_rgb = weft_palette[iy%len(weft_palette)]
    base = np.where(visible_warp[:,:,None],warp_rgb,weft_rgb)
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


def height_map(base,raw_height,s):
    if raw_height is None:
        rgb = linear(base)
        h = rgb[:,:,0]*.2126+rgb[:,:,1]*.7152+rgb[:,:,2]*.0722
    else:
        h = raw_height.copy()
    h = np.clip((h-s['height_low'])/(s['height_high']-s['height_low']),0,1)
    if s['invert']:
        h = 1-h
    if s['height_blur']:
        h = gaussian_filter(h,s['height_blur'],mode='wrap')
    return h.astype(np.float32)


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
    height = height_map(base,raw,s)
    normal = normals(height,s)
    rough = roughness(height,s)
    ao = occlusion(height,s)
    return {'albedo':image(base),'height':image(height),'normal':image(normal*.5+.5),
            'roughness':image(rough),'ao':image(ao),'preview':image(shade(base,normal,rough,ao,s)),
            'orm':image(np.stack([ao,rough,np.zeros_like(ao)],axis=2))}


def weave_info(project):
    """供界面与导出清单使用的循环周期/限制说明。"""
    s = settings(project['settings'])
    px,py = weave_periods(s)
    cx,cy,rx,ry = effective_repeats(s)
    notes = []
    if (rx,ry) != (s['repeat_x'],s['repeat_y']):
        notes.append(f'已按共同周期取整为 {rx}×{ry} 根纱，末尾纱线不截断')
    if rx >= MAX_YARNS or ry >= MAX_YARNS:
        notes.append(f'单方向纱线已达上限 {MAX_YARNS} 根')
    return {'weave':s['weave'],
            'weave_period':{'warp':px,'weft':py},
            'warp_colors':len(s['warp_colors']),'weft_colors':len(s['weft_colors']),
            'common_period':{'warp':cx,'weft':cy},
            'effective_yarns':{'warp':rx,'weft':ry},
            'requested_yarns':{'warp':s['repeat_x'],'weft':s['repeat_y']},
            'limits':{'grid':MAX_GRID,'colors':MAX_COLORS,'yarns':MAX_YARNS},
            'notes':notes}


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
