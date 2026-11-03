import bpy

# Count objects by type
obj_types = {}
for obj in bpy.data.objects:
    t = obj.type
    obj_types[t] = obj_types.get(t, 0) + 1

lights = len([o for o in bpy.data.lights])
cameras = len([o for o in bpy.data.cameras])

result = {
    "object_count": len(bpy.data.objects),
    "type_distribution": obj_types,
    "lights_count": lights,
    "cameras_count": cameras,
    "materials_count": len(bpy.data.materials),
    "meshes_count": len(bpy.data.meshes),
    "collections": [c.name for c in bpy.data.collections]
}
print(result)
