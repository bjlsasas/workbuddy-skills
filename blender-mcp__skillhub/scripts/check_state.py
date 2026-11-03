import bpy

# Check current window material status
window_kw = ['窗', '推拉', '平开', '竖梃', '百叶', '组合窗', '普通窗', '中式窗']
with_glass = 0
frame_only = 0

for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    if any(kw in obj.name for kw in window_kw):
        mats = [s.material.name for s in obj.material_slots if s.material]
        if 'Glass' in mats:
            with_glass += 1
        elif 'Frame' in mats:
            frame_only += 1

print(f'Windows with Glass: {with_glass}')
print(f'Windows frame-only: {frame_only}')
print(f'Materials: {[m.name for m in bpy.data.materials]}')

# Count total vertices and faces
total_verts = sum(len(obj.data.vertices) for obj in bpy.data.objects if obj.type == 'MESH')
total_faces = sum(len(obj.data.polygons) for obj in bpy.data.objects if obj.type == 'MESH')
print(f'Total vertices: {total_verts}')
print(f'Total faces: {total_faces}')
