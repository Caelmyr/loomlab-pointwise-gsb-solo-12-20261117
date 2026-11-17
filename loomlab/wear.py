import numpy as np
from scipy.ndimage import gaussian_filter

WEIGHTS = np.array([.2126, .7152, .0722], dtype=np.float32)


def stretch(value, low, high):
    floor, ceiling = np.percentile(value, [low, high])
    return np.clip((value - floor) / max(float(ceiling - floor), 1e-6), 0., 1.)


def noise(rng, size, sigma):
    # 周期高斯场：白噪声经频域低通，可按材质周期无缝平铺，且只由种子与尺度决定。
    frequencies = np.fft.fftfreq(size)
    kernel = np.exp(-2 * np.pi**2 * sigma**2 *
                    (frequencies[:, None]**2 + frequencies[None, :]**2))
    field = np.fft.ifft2(np.fft.fft2(rng.standard_normal((size, size))) * kernel).real
    return field.astype(np.float32)


def susceptibility(height, detail):
    # 高处、凸转折与过渡坡面更容易被磨到；洼地两端权重都低。
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * .5
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * .5
    elevation = stretch(height, 5, 95)
    ridge = stretch(np.maximum(height - gaussian_filter(height, detail, mode='wrap'), 0), 55, 99.5)
    slope = stretch(np.hypot(dx, dy), 10, 99)
    return (elevation * .60 + ridge * .26 + slope * .14).astype(np.float32)


def mask(height, s):
    size = height.shape[0]
    detail = max(1., size / 180.)
    rng = np.random.default_rng(s['wear_seed'])
    sigma = max(1., s['wear_patch'] * size / 3.)
    coarse = stretch(noise(rng, size, sigma), 1, 99)
    mid = stretch(noise(rng, size, max(1., sigma * .28)), 1, 99)
    fiber = stretch(noise(rng, size, max(1., sigma * .08)), 1, 99)
    # 低洼处被幂次进一步压低；多尺度斑块打散等高线，避免沿组织边缘整齐地磨一圈。
    weight = np.power(susceptibility(height, detail), .7)
    patchiness = .55 * coarse + .34 * mid + .11 * fiber
    score = weight * (.15 + .85 * patchiness**1.3)
    coverage = s['wear_coverage']
    if coverage <= 0:
        return np.zeros_like(height), fiber
    if coverage >= 1:
        return np.ones_like(height), fiber
    low, high = np.percentile(score, [2, 98])
    span = max(float(high - low), 1e-6)
    threshold = np.quantile(score, 1 - coverage) + (coarse - .5) * .10 * span
    step = np.clip((score - threshold) / (.44 * span) + .5, 0, 1)
    return (step * step * (3 - 2 * step)).astype(np.float32), fiber


def apply(base, height, s, wear_mask=None, fiber=None):
    if wear_mask is None or fiber is None:
        wear_mask, fiber = mask(height, s)
    strength = s['wear_strength']
    if strength <= 0 or not wear_mask.any():
        return base, height, np.zeros_like(height)
    effect = (wear_mask * strength)[..., None]
    detail = max(1., height.shape[0] / 180.)
    smooth = gaussian_filter(height, max(1., detail * 1.5), mode='wrap')
    ridge = np.maximum(height - smooth, 0)
    # 磨掉凸起尖端，并在磨损区加入纤维级的细小起伏。
    height = np.clip(height - effect[:, :, 0] * ridge * .55 +
                     effect[:, :, 0] * (fiber - .5) * .05, 0, 1)
    # 底色向浅暖灰褪色，并保留细纤维的明暗变化。
    luminance = base @ WEIGHTS
    pale = np.clip(luminance * .45 + .58, 0, 1)[..., None] * np.array([1., .97, .92], dtype=np.float32)
    worn = np.clip(pale * (.88 + .24 * fiber[..., None]), 0, 1)
    base = np.clip(base * (1 - effect) + worn * effect, 0, 1)
    # 磨损后纤维松散，粗糙度随之升高。
    lift = (wear_mask * strength * (.18 + .16 * fiber)).astype(np.float32)
    return base.astype(np.float32), height.astype(np.float32), lift
