---
name: lcc2-to-blender
description: Load XGrids LCC2 3D Gaussian Splatting scans into Blender as colored point clouds. Use when the user has a `.lcc2` scene (often from a K1 scanner) and wants to view it in Blender, convert it to PLY/USDZ, or explain why MeshLab only shows a white mesh.
agent_created: true
---

# LCC2 to Blender

## Overview

XGrids LCC2 files are a 3D Gaussian Splatting format. The JSON index describes a hierarchy of nodes; color and gaussian data live in `.sog` files, while the companion `.ply` files are plain white meshes. This skill decodes the `.sog` gaussians into a world-space colored point cloud and sets up Blender to render it with per-point colors.

## When to Use

- User asks to open/view a `.lcc2` or LCC2 export folder in Blender.
- MeshLab shows only a white mesh and the user wants to know why.
- User wants a colored export (NPZ/PLY/OBJ/USDZ) from an LCC2 scan.
- User mentions XGrids, K1 scanner, LCC Studio, or `.sog` files.

## Quick Workflow

1. **Locate the LCC2 scene folder.** The `.lcc2` file is at the root; `data/3dgs/` contains `.sog` files; `data/mesh/` contains white `.ply` chunks.
2. **Decode the colored point cloud.**
   - Run `scripts/decode_lcc2.py <scene.lcc2> [output.npz]`.
   - The script maps each `.sog` to its sibling `.ply` bbox to compute correct world coordinates.
3. **Import into Blender.**
   - Create a mesh with one vertex per point.
   - Store colors in a vertex color attribute named `Col`.
   - Use Geometry Nodes: `Named Attribute 'Col'` → `Instance on Points` (icosphere) → `Store Named Attribute` (domain `INSTANCE`, name `instance_col`, type `FLOAT_COLOR`) → `Realize Instances` → `Set Material`.
   - Material: Emission shader reading `instance_col` for bright, lighting-independent colored points.
4. **Frame the camera** using the 1st–99th percentile bounding box to avoid outliers.
5. **Render** with CYCLES or EEVEE; use `AgX` view transform and a dark world background for clarity.

## Decoding Reference

- Position encoding: `means_u16 = (means_u << 8) | means_l`; `local = bbox_min + (means_u16 / 65535) * (bbox_max - bbox_min)`.
- Color encoding: `sh0` stores `rgb * 0.15 + 0.5`; recover with `(pixel - 0.5) / 0.15`.
- Recommended tone-map: `np.tanh(colors * 0.6) * 0.5 + 0.5` to bring SH coefficients into `[0,1]`.

## Known Pitfalls

- Do not rely on the `.ply` files for color — they contain only `x y z`.
- Blender 5.2 `mesh.vertices.foreach_set('co', ...)` may silently fail; set vertices in a loop or convert to Point Cloud afterward.
- Point Cloud objects do not display vertex colors in Solid view; use the Geometry Nodes instancing approach above.
- LCC Viewer V1.x expects `.lcc`, not `.lcc2`; use this conversion path instead.

## Resources

- `scripts/decode_lcc2.py` — decode `.lcc2` + `.sog` into `positions/colors` NPZ.
