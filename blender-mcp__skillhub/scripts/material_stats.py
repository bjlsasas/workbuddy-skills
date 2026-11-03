import bpy

mat_names = [m.name for m in bpy.data.materials]
obj_with_mat = []
for obj in bpy.data.objects:
    if obj.data and hasattr(obj.data, 'material_slots'):
        mats = [ms.material.name for ms in obj.data.material_slots if ms.material]
        if mats:
            obj_with_mat.append({"name": obj.name, "materials": mats})

result = {
    "materials": mat_names,
    "objects_with_materials": obj_with_mat[:20]
}
print(result)
