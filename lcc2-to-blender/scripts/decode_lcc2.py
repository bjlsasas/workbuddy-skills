#!/usr/bin/env python3
"""
Decode XGrids LCC2 3D Gaussian Splatting scene into a world-space colored point cloud.
Outputs a .npz file with 'positions' and 'colors' arrays.

Usage:
    python decode_lcc2.py <path/to/scene.lcc2> [output.npz]
"""
import json, os, sys, zipfile, math
from pathlib import Path
import numpy as np
from PIL import Image


def decode_sog(sog_path, bbox_min, bbox_max):
    """Decode one .sog file (SPZ-like ZIP with WebP channels)."""
    with zipfile.ZipFile(sog_path, 'r') as z:
        means_l = np.array(Image.open(z.open('means_l.webp'))).astype(np.uint16)
        means_u = np.array(Image.open(z.open('means_u.webp'))).astype(np.uint16)
        sh0 = np.array(Image.open(z.open('sh0.webp'))).astype(np.float32) / 255.0
        meta = json.loads(z.read('meta.json'))

    # positions: 16-bit split stored in two webp files
    means_u16 = (means_u << 8) | means_l
    t = means_u16.reshape(-1, 1) / 65535.0
    local = bbox_min + t * (bbox_max - bbox_min)

    # colors: sh0 stores (rgb * 0.15 + 0.5)
    colors = (sh0.reshape(-1, 4)[:, :3] - 0.5) / 0.15
    # tone-map into [0,1]
    colors = np.tanh(colors * 0.6) * 0.5 + 0.5
    return local, colors


def ply_bbox(ply_path):
    """Return min/max of a PLY file's vertices (header must be ascii)."""
    with open(ply_path, 'rb') as f:
        header = b''
        while b'end_header' not in header:
            header += f.readline()
        nvert = None
        for line in header.decode('ascii', errors='ignore').splitlines():
            if line.startswith('element vertex'):
                nvert = int(line.split()[-1])
                break
        arr = np.fromfile(f, dtype=np.float32, count=nvert * 3, sep=' ').reshape(-1, 3)
    return arr.min(axis=0), arr.max(axis=0)


def main():
    lcc2_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else lcc2_path.with_suffix('.npz')
    root = lcc2_path.parent

    with open(lcc2_path, 'r', encoding='utf-8') as f:
        scene = json.load(f)

    root_min = np.array(scene['root']['boundingBox']['min'], dtype=np.float32)
    root_max = np.array(scene['root']['boundingBox']['max'], dtype=np.float32)

    # Map each splat file to the bbox of its sibling mesh file when available.
    all_positions = []
    all_colors = []
    mesh_dir = root / 'data' / 'mesh'
    sog_dir = root / 'data' / '3dgs'

    for node in scene['root'].get('child', []):
        for splat in node.get('splatFiles', []):
            sog_name = Path(splat).name
            sog_file = sog_dir / sog_name
            if not sog_file.exists():
                continue
            # Try to find a matching mesh file to get correct world offset.
            mesh_name = None
            if node.get('meshFiles'):
                mesh_name = Path(node['meshFiles'][0]).name
            elif sog_name.replace('.sog', '.ply') in [p.name for p in mesh_dir.glob('*.ply')]:
                mesh_name = sog_name.replace('.sog', '.ply')
            elif '_' in sog_name and sog_name.split('_')[0] + '_0.ply' in [p.name for p in mesh_dir.glob('*.ply')]:
                mesh_name = sog_name.split('_')[0] + '_0.ply'

            if mesh_name and (mesh_dir / mesh_name).exists():
                mn, mx = ply_bbox(mesh_dir / mesh_name)
            else:
                mn, mx = root_min, root_max

            pos, col = decode_sog(sog_file, mn, mx)
            all_positions.append(pos)
            all_colors.append(col)
            print(f"Decoded {sog_name}: {len(pos)} points")

    positions = np.vstack(all_positions).astype(np.float32)
    colors = np.vstack(all_colors).astype(np.float32)
    np.savez_compressed(out_path, positions=positions, colors=colors)
    print(f"Saved {len(positions)} points to {out_path}")


if __name__ == '__main__':
    main()
