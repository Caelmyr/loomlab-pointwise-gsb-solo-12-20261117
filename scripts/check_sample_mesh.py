#!/usr/bin/env python3
"""Check LoomLab relief sample exports: watertight OBJ, exact size, closed flat plate."""
import io
import sys
import numpy as np
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loomlab.maps import settings as material_settings, weave, height_map
from loomlab.sample import parameters, write, budget


def analyse(raw):
    vertices, faces = [], []
    for line in raw.decode('ascii').splitlines():
        if line.startswith('v '):
            vertices.append([float(value) for value in line.split()[1:]])
        elif line.startswith('f '):
            faces.append([int(value) for value in line.split()[1:]])
    points = np.array(vertices, dtype=np.float64)
    edges = Counter((a, b) for face in faces for a, b in zip(face, face[1:] + face[:1]))
    duplicated = sum(count > 1 for count in edges.values())
    unpaired = sum((b, a) not in edges for a, b in edges)
    volume = sum(np.dot(points[face[0] - 1], np.cross(points[face[k] - 1], points[face[k + 1] - 1])) / 6
                 for face in faces for k in (1, 2))
    return points, faces, duplicated, unpaired, volume


def case(name, height, p):
    stream = io.BytesIO()
    write(stream, height, p)
    points, faces, duplicated, unpaired, volume = analyse(stream.getvalue())
    expected = p['width'] * p['length'] * (p['base'] + p['relief'] * float(np.mean(height)))
    assert duplicated == 0, (name, 'repeated directed edge')
    assert unpaired == 0, (name, 'edge without opposite')
    assert len(faces) == 2 * (p['mesh'] - 1) ** 2 + 4 * (p['mesh'] - 1), (name, 'face count')
    assert abs(points[:, 0].min()) < 1e-9 and abs(points[:, 0].max() - p['width']) < 1e-9, (name, 'width')
    assert abs(points[:, 1].min()) < 1e-9 and abs(points[:, 1].max() - p['length']) < 1e-9, (name, 'length')
    assert abs(points[:, 2].min()) < 1e-9, (name, 'base')
    assert abs(volume - expected) <= expected * 1e-3, (name, 'volume', volume, expected)
    print(f'{name}: {len(points)} vertices, {len(faces)} faces, volume {volume:.2f} mm³')


def main():
    for mesh in (16, 17, 192):
        flat = np.full((256, 256), .5, np.float32)
        case(f'flat-{mesh}', flat, {'width': 60., 'length': 40., 'base': 2., 'relief': 3., 'mesh': mesh})
    s = material_settings({'size': 128, 'sample_width': 80., 'sample_length': 55.,
                           'sample_base': 1.5, 'sample_relief': 4., 'sample_mesh': 96})
    base, raw = weave(s)
    height = height_map(base, raw, s)
    p = parameters(s)
    case('weave-96', height, p)
    report = budget(p)
    assert not report['over_budget'] and report['faces'] == 2 * 95 ** 2 + 4 * 95
    assert budget({**p, 'mesh': 512})['over_budget'], 'budget should warn above limit'
    print('budget warnings:', budget({**p, 'mesh': 512})['warnings'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
