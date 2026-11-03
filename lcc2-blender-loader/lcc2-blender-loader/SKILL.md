---
name: lcc2-blender-loader
description: Load XGrids LCC2 3D reconstruction results (e.g. from K1 scanners) into Blender via the Blender MCP connector. Handles .lcc2 JSON scene trees, .ply mesh chunks, and .sog Gaussian splat files. Trigger this skill when a user mentions opening, viewing, importing, or loading files with .lcc2 extension, LCC2 format, XGrids reconstructions, or directories containing data/3dgs, data/mesh, info/report.json structures.
agent_created: true
---

# LCC2 Blender Loader

## Overview

This skill loads XGrids LCC2 3D reconstruction exports into the currently running Blender instance through the Blender MCP connector. LCC2 exports typically contain:

- `*.lcc2` — JSON scene index with bounding-box hierarchy and LOD info
- `data/mesh/*.ply` — reconstructed mesh chunks in world coordinates
- `data/mesh/*.btree` — BVH acceleration structures (not directly needed for viewing)
- `data/3dgs/*.sog` — 3D Gaussian Splatting chunks (for viewers that support .sog)
- `info/report.json` — scan metadata (point count, device, duration)
- `info/poses.json` — camera/image poses used for reconstruction

The loader imports the `.ply` mesh chunks, assigns a unified material, and sets up a camera + lighting so the scene can be viewed immediately.

## When to Use

- User asks to open an `.lcc2` file in Blender
- User has a folder with `data/mesh/*.ply` and `厕所.lcc2` or similar
- User mentions XGrids, K1 scanner, LCC2, `.sog`, or 3D Gaussian Splatting exports
- User wants to convert a scanned room/building into a Blender scene

## Pre-requisites

1. Blender is running with the `blender_mcp` addon enabled and its MCP server started (default port 9876).
2. WorkBuddy has a Blender MCP connector configured in `~/.workbuddy/mcp.json` (NOT `.mcp.json`).
3. The connector is trusted/activated in WorkBuddy's MCP service management page.

## Workflow

### Step 1: Verify/fix MCP configuration

Check that `~/.workbuddy/mcp.json` contains the Blender MCP server:

```json
{
  "mcpServers": {
    "blender": {
      "type": "stdio",
      "command": "uvx",
      "args": ["blender-mcp"]
    }
  }
}
```

If the configuration is only in `~/.workbuddy/.mcp.json`, merge it into `mcp.json` and back up the dot file. WorkBuddy reads the non-dot `mcp.json`.

### Step 2: Verify Blender MCP is reachable

Use `mcp__blender__get_scene_info` to confirm the connector is active. If it returns empty `objects: []`, the scene is ready to populate.

### Step 3: Parse the LCC2 index

Read the `.lcc2` file as JSON. Extract:

- `scene['name']`
- `scene['totalSplats']`
- `scene['root']['boundingBox']`
- All leaf nodes in the `root` tree that contain `data`
- Mapping from node id (e.g. `0_5`) to `.ply` filename (`0_5.ply`)

### Step 4: Import mesh chunks

For each `.ply` in `data/mesh/`:

1. Call `bpy.ops.wm.ply_import(filepath=<ply_path>)`
2. Rename imported object to `mesh_<node_id>`
3. Apply the shared material

The PLY vertices are already in world coordinates, matching the LCC2 bounding boxes. No additional translation is required.

### Step 5: Scene setup

- Create a single principled BSDF material with neutral color and moderate roughness
- Add a `SUN` light and an `AREA` light for indoor scenes
- Add a camera positioned diagonally outside the scene bounding box, looking at the center
- Switch render engine to `CYCLES` with 64 samples
- Set world background to light gray

### Step 6: Verification

Use `mcp__blender__get_viewport_screenshot` to capture and review the loaded scene.

## Important Notes

- `.sog` Gaussian splat files are not imported by this loader because Blender does not natively support the `.sog` format. Use a 3D Gaussian Splatting viewer for those.
- The original mesh chunks remain as separate Blender objects. To edit as a single mesh, join them with `bpy.ops.object.join()`.
- Files are edited in-place in the current Blender scene. Do not save the `.blend` unless the user explicitly requests it.

## Resources

- `scripts/load_lcc2_scene.py` — standalone BPY script that parses an LCC2 directory and imports all mesh chunks. Can be run directly via `mcp__blender__execute_blender_code`.
