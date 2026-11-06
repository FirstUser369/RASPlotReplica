from pathlib import Path
import sys
import ezdxf

root = Path(__file__).resolve().parents[1]
doc = ezdxf.readfile(str(root / 'RAS Plot Database and Model Linework' / 'Model Exported Linework.dxf'))
insert = next(e for e in doc.modelspace() if e.dxftype() == 'INSERT' and e.dxf.layer == 'GRIDS')
print('INSERT')
print('name', insert.dxf.name)
print('insert', tuple(insert.dxf.insert))
print('scale', insert.dxf.xscale, insert.dxf.yscale)
print('rotation', insert.dxf.rotation)

for name in [insert.dxf.name, 'BORDER']:
    block = doc.blocks[name]
    print('BLOCK', name)
    print('base_point', tuple(block.base_point))
    for entity in block:
        kind = entity.dxftype()
        if kind == 'LINE':
            print(kind, entity.dxf.layer, tuple(entity.dxf.start), tuple(entity.dxf.end))
        elif kind == 'LWPOLYLINE':
            print(kind, entity.dxf.layer, [(float(p[0]), float(p[1])) for p in entity.get_points()])
        elif kind == 'TEXT':
            print(kind, entity.dxf.layer, repr(entity.dxf.text), tuple(entity.dxf.insert), entity.dxf.height, getattr(entity.dxf, 'rotation', 0))
        else:
            print(kind, entity.dxf.layer)
