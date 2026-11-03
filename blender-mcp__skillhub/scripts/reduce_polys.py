import bpy
import bmesh

print('=== Starting Geometry Reduction ===')

# Record initial state
total_verts_before = sum(len(obj.data.vertices) for obj in bpy.data.objects if obj.type == 'MESH')
total_faces_before = sum(len(obj.data.polygons) for obj in bpy.data.objects if obj.type == 'MESH')

# Step 1: Handle windows - remove glass material assignment, keep frame only
window_kw = ['窗', '推拉', '平开', '竖梃', '百叶', '组合窗', '普通窗', '中式窗']
windows_processed = 0
for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    if any(kw in obj.name for kw in window_kw):
        # Remove Glass material from slots
        mats = obj.data.materials
        # Build new list without Glass
        new_mats = [m for m in mats if not (m and m.name == 'Glass')]
        # Clear and rebuild
        mats.clear()
        for m in new_mats:
            mats.append(m)
        windows_processed += 1

print(f'Windows processed: {windows_processed} (Glass material removed)')

# Step 2: For all mesh objects, clean geometry
cleaned = 0
degenerate_removed = 0

for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    mesh = obj.data
    if len(mesh.polygons) < 2:
        continue

    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Remove doubles (merge vertices that are too close)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)

    # Remove degenerate faces (less than 3 vertices)
    degenerate = [f for f in bm.faces if len(f.verts) < 3]
    if degenerate:
        bmesh.ops.delete(bm, geom=degenerate, context='FACES')
        degenerate_removed += len(degenerate)

    # Update mesh
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    cleaned += 1

print(f'Meshes cleaned: {cleaned}')
print(f'Degenerate faces removed: {degenerate_removed}')

# Step 3: Dissolve internal edges in wall and column objects
wall_col_keywords = ['基本墙', '混凝土_矩形_梁', '混凝土_矩形_柱', '混凝土_圆形_柱']
internal_edges_dissolved = 0

for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    if not any(kw in obj.name for kw in wall_col_keywords):
        continue

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Find internal edges (edges shared by 2+ faces, not on boundary)
    internal_edges = [e for e in bm.edges if not e.is_boundary]
    if internal_edges:
        bmesh.ops.dissolve_edges(bm, edges=internal_edges, use_verts=False)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        internal_edges_dissolved += len(internal_edges)

print(f'Internal edges dissolved in wall/column objects: {internal_edges_dissolved}')

# Final stats
total_verts_after = sum(len(obj.data.vertices) for obj in bpy.data.objects if obj.type == 'MESH')
total_faces_after = sum(len(obj.data.polygons) for obj in bpy.data.objects if obj.type == 'MESH')

print(f'\n=== Final Stats ===')
print(f'Vertices: {total_verts_before} -> {total_verts_after} (removed {total_verts_before - total_verts_after})')
print(f'Faces: {total_faces_before} -> {total_faces_after} (removed {total_faces_before - total_faces_after})')
print(f'Reduction: {(1 - total_faces_after/total_faces_before)*100:.1f}%')
