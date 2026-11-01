"""Preserve Tripo FBX and derive a GLB locally; no paid API conversion."""
import bpy
import json
import sys
from pathlib import Path
from mathutils import Vector

p = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(p / 'raw.fbx'))
meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
counts = {}
for o in meshes:
    for polygon in o.data.polygons:
        n = str(len(polygon.vertices))
        counts[n] = counts.get(n, 0) + 1
points = [o.matrix_world @ Vector(v) for o in meshes for v in o.bound_box]
stats = {'polygon_vertex_counts': counts, 'total_polygons': sum(counts.values()),
         'blender_dimensions': [max(v[i] for v in points)-min(v[i] for v in points) for i in range(3)],
         'objects': [o.name for o in meshes], 'images': [i.name for i in bpy.data.images]}
(p / 'fbx-inspection.json').write_text(json.dumps(stats,indent=2)+'\n')
# Retain all image inputs independently of the exchange file.
textures = p / 'fbx-textures'
textures.mkdir(exist_ok=True)
for image in bpy.data.images:
    if image.packed_file:
        image.filepath_raw = str(textures / Path(image.name).name)
        image.save()
bpy.ops.export_scene.gltf(filepath=str(p / 'converted.glb'),export_format='GLB')
