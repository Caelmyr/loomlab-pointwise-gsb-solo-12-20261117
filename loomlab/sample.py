import numpy as np
from .maps import resample

# 顶点预算按OBJ文本与内存估算；超出时提前提醒，绝不静默降低用户选择的尺寸或起伏
VERTEX_LIMIT = 250000
# 表面单元长宽比超过该值时形变明显，提示用户
SLIVER_RATIO = 8
# 预览最多输出的网格行数，避免响应过大
PREVIEW_ROWS = 96


def parameters(settings):
    return {'width':settings['sample_width'],'length':settings['sample_length'],
            'base':settings['sample_base'],'relief':settings['sample_relief'],
            'mesh':settings['sample_mesh']}


def budget(p):
    width,length,base,relief,mesh = p['width'],p['length'],p['base'],p['relief'],p['mesh']
    cells = mesh-1
    vertices = 2*mesh*mesh
    faces = 2*cells*cells+4*cells
    cell_width = width/cells
    cell_length = length/cells
    ratio = max(cell_width,cell_length)/min(cell_width,cell_length)
    warnings = []
    if cell_width+cell_length < relief/3:
        warnings.append('单格边长之和小于最大起伏的三分之一，打印可能丢失细节；建议提高网格密度或降低起伏')
    if ratio > SLIVER_RATIO:
        warnings.append('长宽相差较大，表面单元会呈狭长形；建议调整长宽比或提高网格密度')
    if min(cell_width,cell_length) < .05:
        warnings.append('单格小于0.05毫米，多数打印设备无法分辨')
    return {'vertices':vertices,'faces':faces,'cells':cells,
            'cell_width':round(cell_width,4),'cell_length':round(cell_length,4),
            'cell_ratio':round(ratio,2),'limit':VERTEX_LIMIT,
            'over_budget':vertices > VERTEX_LIMIT,
            'estimated_megabytes':round(vertices*48/1048576,1),
            'warnings':warnings}


def mesh_data(height,p):
    # 顶面高度（毫米）来自当前材质高度场，底板厚度固定从 z=0 到 z=base
    surface = resample(height,p['mesh'])
    z = p['base']+surface*p['relief']
    return {'width':p['width'],'length':p['length'],'base':p['base'],
            'relief':p['relief'],'mesh':p['mesh'],'z':z,
            'minimum':float(z.min()),'maximum':float(z.max())}


def preview(height,p):
    data = mesh_data(height,p)
    z = data.pop('z')
    # 降采样后仍保留真实尺寸范围与高度范围，网格形状由原始mesh决定
    step = max(1,int(np.ceil((z.shape[0]-1)/PREVIEW_ROWS)))
    rows = np.unique(np.r_[np.arange(0,z.shape[0],step),z.shape[0]-1])
    return {**data,'sampled_rows':int(rows.size),'step':int(rows[1]-rows[0]) if rows.size > 1 else 1,
            'surface':np.round(z[np.ix_(rows,rows)],3).tolist(),
            'edge_x':np.round(z[rows,0],3).tolist(),
            'edge_y':np.round(z[0,rows],3).tolist(),
            'budget':budget(p)}


def build(height,p):
    """闭合实体：顶面（材质高度）、底面与四周侧壁共享同一圈顶点，无重复边。"""
    n = p['mesh']
    width,length,base,relief = p['width'],p['length'],p['base'],p['relief']
    z = base+resample(height,n)*relief
    x = (np.arange(n,dtype=np.float64)/(n-1))*width
    y = (np.arange(n,dtype=np.float64)/(n-1))*length
    grid_x,grid_y = np.meshgrid(x,y)
    top = np.stack([grid_x,grid_y,z],axis=2).reshape(-1,3)
    bottom = np.stack([grid_x,grid_y,np.zeros_like(grid_x)],axis=2).reshape(-1,3)
    offset = n*n
    cells = n-1
    # 顶面按单元角点逆时针环绕（法线+Z），底面反向（法线-Z）；
    # 侧壁沿同一条边界以相反方向连接顶底，保证每条无向边恰好被两个面共享。
    index = np.arange(cells)
    a = index[:,None]*n+index[None,:]
    quad = np.stack([a,a+1,a+n+1,a+n],axis=2).reshape(-1,4)+1
    faces = [quad,quad[:,::-1]+offset]
    west = index*n
    east = index*n+n-1
    south = index
    north = (n-1)*n+index
    faces.append(np.stack([west,west+n,west+n+offset,west+offset],axis=1)+1)
    faces.append(np.stack([east+n,east,east+offset,east+n+offset],axis=1)+1)
    faces.append(np.stack([south+1,south,south+offset,south+1+offset],axis=1)+1)
    faces.append(np.stack([north,north+1,north+1+offset,north+offset],axis=1)+1)
    return top,bottom,np.concatenate(faces,axis=0).astype(np.int64)


def write(stream,height,p,name='relief_sample'):
    top,bottom,faces = build(height,p)
    header = ['# LoomLab embroidery relief sample',
              f'# width={p["width"]:g}mm length={p["length"]:g}mm base={p["base"]:g}mm '
              f'relief={p["relief"]:g}mm mesh={p["mesh"]}',
              f'o {name}']
    stream.write(('\n'.join(header)+'\n').encode('ascii'))
    vertices = np.concatenate([top,bottom],axis=0)
    # 分块写入，控制大网格导出时的峰值内存
    for start in range(0,len(vertices),4096):
        block = vertices[start:start+4096]
        stream.write(''.join('v %.6f %.6f %.6f\n'%tuple(row) for row in block).encode('ascii'))
    for start in range(0,len(faces),4096):
        block = faces[start:start+4096]
        stream.write(''.join('f %d %d %d %d\n'%tuple(row) for row in block).encode('ascii'))
