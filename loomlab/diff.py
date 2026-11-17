"""只读的材质版本对照：对齐、差异图与差异统计。

所有函数都不写入 Catalog；两个对照对象（当前状态或某个历史版本）都从已
保存的设置与不可变源图临时渲染。法线按向量方向比较，先统一 Y 约定，因此
纯粹切换 normal_y 不会被当成表面变化；尺寸不同时用包裹双线性重采样对齐，
保证同一材质位置（周期纹理坐标）可比较。
"""
import math
import numpy as np
from PIL import Image
from image_lab.validation import Invalid
from .maps import render

# 对照只面向五个交付通道；preview 受光照设置影响、orm 是打包通道，不参与。
CHANNELS = ('albedo', 'height', 'normal', 'roughness', 'ao')

_RAMP_STOPS = {
    # 有符号标量：负=蓝、零=中性灰、正=暖色
    'diverging': [(0.00, (38, 78, 112)), (0.50, (236, 233, 224)), (1.00, (158, 58, 36))],
    # 幅度/角度：深底到亮黄，越亮差异越大
    'heat': [(0.00, (24, 28, 30)), (0.35, (52, 84, 99)), (0.70, (148, 132, 72)), (1.00, (240, 226, 150))],
}


def _array(picture):
    return np.asarray(picture, dtype=np.float32) / 255.0


def _ramp(values, kind):
    """把 [0,1] 数组按分段线性色带映射成 RGB（uint8）。"""
    values = np.clip(values, 0.0, 1.0)
    stops = _RAMP_STOPS[kind]
    out = np.zeros(values.shape + (3,), dtype=np.float32)
    for index, (pos, color) in enumerate(stops):
        if index == 0:
            next_pos = stops[1][0]
            weight = np.clip((next_pos - values) / (next_pos - pos), 0.0, 1.0)
        elif index == len(stops) - 1:
            prev_pos = stops[index - 1][0]
            weight = np.clip((values - prev_pos) / (pos - prev_pos), 0.0, 1.0)
        else:
            prev_pos, _ = stops[index - 1]
            next_pos, _ = stops[index + 1]
            rise = np.clip((values - prev_pos) / (pos - prev_pos), 0.0, 1.0)
            fall = np.clip((next_pos - values) / (next_pos - pos), 0.0, 1.0)
            weight = np.minimum(rise, fall)
        out += weight[:, :, None] * np.array(color, dtype=np.float32)
    return Image.fromarray(np.round(np.clip(out, 0, 255)).astype('uint8'), 'RGB')


def _diverging(signed, gain):
    # 0 差异 → 0.5（中性灰）；±gain → 0 / 1（蓝 / 暖色）
    return _ramp(np.clip(0.5 + signed / (2.0 * gain), 0.0, 1.0), 'diverging')


def resize_wrap(picture, size):
    """包裹边界的双线性重采样，保持周期纹理的材质坐标对应。"""
    picture = picture.convert('RGB')
    if picture.width == size and picture.height == size:
        return picture
    src = np.asarray(picture, dtype=np.float32)
    h, w = src.shape[:2]
    gy, gx = np.mgrid[:size, :size].astype(np.float32)
    u = (gx + 0.5) / size * w - 0.5
    v = (gy + 0.5) / size * h - 0.5
    x0 = np.floor(u).astype(np.int64)
    y0 = np.floor(v).astype(np.int64)
    tx = (u - x0)[:, :, None]
    ty = (v - y0)[:, :, None]
    x1, y1 = x0 + 1, y0 + 1
    # 周期纹理：越界回绕而不是补边
    top = src[y0 % h, x0 % w] * (1 - tx) + src[y0 % h, x1 % w] * tx
    bottom = src[y1 % h, x0 % w] * (1 - tx) + src[y1 % h, x1 % w] * tx
    return Image.fromarray(np.round(np.clip(top * (1 - ty) + bottom * ty, 0, 255)).astype('uint8'), 'RGB')


def view_material(p, snapshot):
    """用快照设置构造一个临时材质对象（不触碰当前材质）。"""
    return {'id': p['id'], 'name': p['name'], 'revision': p['revision'],
            'mode': snapshot['mode'], 'settings': snapshot['settings'],
            'source': snapshot.get('source'), 'snapshots': p.get('snapshots', [])}


def resolve_view(catalog, p, view):
    """view 为 'current' 或快照 id；返回 (渲染贴图集合, 视图描述)。"""
    if view == 'current':
        return render(p, catalog.source(p)), {'view': 'current', 'label': '当前编辑状态',
                                              'size': p['settings']['size'],
                                              'normal_y': p['settings']['normal_y']}
    snap = next((v for v in p.get('snapshots', []) if v['id'] == view), None)
    if snap is None:
        raise Invalid('对照版本不存在')
    material = view_material(p, snap)
    source = catalog.asset(p['id'], snap.get('source'))
    return render(material, source), {'view': snap['id'], 'label': snap['label'],
                                      'size': snap['settings']['size'],
                                      'normal_y': snap['settings']['normal_y']}


def _auto_gain(absolute, floor):
    gain = float(np.nanpercentile(absolute, 95))
    if not math.isfinite(gain) or gain < floor:
        gain = floor
    return gain


def _scalar_stats(diff):
    absolute = np.abs(diff)
    return {'mean_abs': float(absolute.mean()), 'max_abs': float(absolute.max()),
            'rms': float(np.sqrt(np.mean(diff ** 2))),
            'mean_signed': float(diff.mean()),
            'p95_abs': float(np.nanpercentile(absolute, 95))}


def _normal_vectors(picture):
    """从 8-bit 编码法线图还原单位向量；返回 (整数编码, 单位向量)。"""
    codes = np.asarray(picture.convert('RGB'), dtype=np.int16)
    vectors = codes.astype(np.float32) / 127.5 - 1.0
    vectors /= np.maximum(np.linalg.norm(vectors, axis=2, keepdims=True), 1e-8)
    return codes, vectors


def compare(catalog, p, view_a, view_b, channel):
    if channel not in CHANNELS:
        raise Invalid('对照通道支持 albedo/height/normal/roughness/ao')
    maps_a, meta_a = resolve_view(catalog, p, view_a)
    maps_b, meta_b = resolve_view(catalog, p, view_b)
    target = min(meta_a['size'], meta_b['size'])
    aligned = {'grid': target,
               'a_size': meta_a['size'], 'b_size': meta_b['size'],
               'a_normal_y': meta_a['normal_y'], 'b_normal_y': meta_b['normal_y']}
    result = {'channel': channel, 'a': meta_a, 'b': meta_b, 'aligned': aligned}

    if channel == 'normal':
        # 在整数编码层面处理 Y 约定：down 与 up 的关系是 g' = 255-g，
        # 先翻转编码再解码，纯约定切换可精确抵消，不会残留整图差异。
        _, vec_a = _normal_vectors(resize_wrap(maps_a[channel], target))
        codes_b, _ = _normal_vectors(resize_wrap(maps_b[channel], target))
        if meta_a['normal_y'] != meta_b['normal_y']:
            codes_b[:, :, 1] = 255 - codes_b[:, :, 1]
            aligned['normal_y_unified_to'] = meta_a['normal_y']
        vec_b = codes_b.astype(np.float32) / 127.5 - 1.0
        vec_b /= np.maximum(np.linalg.norm(vec_b, axis=2, keepdims=True), 1e-8)
        dot = np.clip(np.sum(vec_a * vec_b, axis=2), -1.0, 1.0)
        angle_deg = np.degrees(np.arccos(dot))
        gain = max(float(np.nanpercentile(angle_deg, 95)), 0.5)
        result['picture'] = _ramp(angle_deg / gain, 'heat')
        result['gain_deg'] = gain
        result['stats'] = {'mean_deg': float(angle_deg.mean()),
                           'max_deg': float(angle_deg.max()),
                           'p95_deg': float(np.nanpercentile(angle_deg, 95)),
                           'over_1_deg_pct': float(np.mean(angle_deg > 1.0) * 100),
                           'over_5_deg_pct': float(np.mean(angle_deg > 5.0) * 100)}
        return result

    pic_a = resize_wrap(maps_a[channel], target)
    pic_b = resize_wrap(maps_b[channel], target)
    a, b = _array(pic_a), _array(pic_b)

    if channel == 'albedo':
        delta = b - a  # B 相对 A 的变化
        magnitude = np.sqrt(np.mean(delta ** 2, axis=2))
        gain = _auto_gain(magnitude, 0.01)
        result['picture'] = _ramp(magnitude / gain, 'heat')
        result['gain'] = gain
        result['stats'] = {'mean_abs': float(magnitude.mean()),
                           'max_abs': float(magnitude.max()),
                           'rms': float(np.sqrt(np.mean(delta ** 2))),
                           'p95_abs': float(np.nanpercentile(magnitude, 95)),
                           'channel_mean_delta': {c: float(delta[:, :, i].mean())
                                                  for i, c in enumerate(('r', 'g', 'b'))}}
        return result

    # height / roughness / ao：有符号标量差异（B-A），蓝减红增
    delta = b[:, :, 0] - a[:, :, 0]
    gain = _auto_gain(np.abs(delta), 0.01)
    result['picture'] = _diverging(delta, gain)
    result['gain'] = gain
    result['stats'] = _scalar_stats(delta)
    return result
