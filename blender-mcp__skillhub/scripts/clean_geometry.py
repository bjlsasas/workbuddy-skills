import bpy
import bmesh

# Step 1: Delete glass faces from windows (horizontal faces with normal Z>0.9)
windows_cleaned = 0
total_glass_faces_deleted = 0
for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    mats = [s.material.name for s in obj.material_slots if s.material]
    if 'Glass' not in mats:
        continue

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    glass_faces = []
    for face in bm.faces:
        if abs(face.normal.z) > 0.9:
            glass_faces.append(face)

    if glass_faces:
        bmesh.ops.delete(bm, geom=glass_faces, context='FACES')
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        windows_cleaned += 1
        total_glass_faces_deleted += len(glass_faces)

print(f'Windows cleaned: {windows_cleaned}')
print(f'Glass faces deleted: {total_glass_faces_deleted}')

# Step 2: Remove double faces and clean up each mesh
total_verts_before = sum(len(obj.data.vertices) for obj in bpy.data.objects if obj.type == 'MESH')
total_faces_before = sum(len(obj.data.polygons) for obj in bpy.data.objects if obj.type == 'MESH')

cleaned_count = 0
for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    mesh = obj.data
    if len(mesh.polygons) < 2:
        continue
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Remove doubles
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)

    # Dissolve degenerate faces
    degenerate = [f for f in bm.faces if len(f.verts) < 3]
    if degenerate:
        bmesh.ops.delete(bm, geom=degenerate, context='FACES')

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    cleaned_count += 1

print(f'Meshes cleaned: {cleaned_count}')

# Final count
total_verts_after = sum(len(obj.data.vertices) for obj in bpy.data.objects if obj.type == 'MESH')
total_faces_after = sum(len(obj.data.polygons) for obj in bpy.data.objects if obj.type == 'MESH')

print(f'\nFinal stats:')
print(f'Vertices: {total_verts_before} -> {total_verts_after} (removed {total_verts_before - total_verts_after})')
print(f'Faces: {total_faces_before} -> {total_faces_after} (removed {total_faces_before - total_faces_after})')
