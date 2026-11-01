"""Normalise a Tripo GLB to the GeometryForge fleet contract and extract its textures.

Fleet contract (low_poly_fleet README): metres, +Y up, +Z nose/forward, +X right,
pivot at the bounds centre, length taken from the recipe. glTF is already +Y up and metres;
what varies per generator is where the nose points and the overall scale.

Preferred method is "baked": the transform is written into the vertex data and every node
transform is reset to identity, so Unreal's importer has nothing extra to interpret.
If the file can't be baked safely, a root node carries the transform instead ("root-node").
"""

from __future__ import annotations

import base64
import io
import json
import math
import struct
from pathlib import Path

from PIL import Image
from pygltflib import GLTF2, Node

FLOAT = 5126
TRIANGLES = 4
Matrix = list[list[float]]


# ---- small 4x4 helpers (row-major) -------------------------------------------------------

def identity() -> Matrix:
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def matmul(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def apply_point(m: Matrix, p: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = p
    return tuple(m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3] for r in range(3))


def from_column_major(values: list[float]) -> Matrix:
    return [[values[c * 4 + r] for c in range(4)] for r in range(4)]


def to_column_major(m: Matrix) -> list[float]:
    return [m[r][c] for c in range(4) for r in range(4)]


def node_matrix(node: Node) -> Matrix:
    if node.matrix:
        return from_column_major(node.matrix)
    tx, ty, tz = node.translation or (0.0, 0.0, 0.0)
    qx, qy, qz, qw = node.rotation or (0.0, 0.0, 0.0, 1.0)
    sx, sy, sz = node.scale or (1.0, 1.0, 1.0)
    rot = [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ]
    m = identity()
    for r in range(3):
        m[r][0], m[r][1], m[r][2] = rot[r][0] * sx, rot[r][1] * sy, rot[r][2] * sz
    m[0][3], m[1][3], m[2][3] = tx, ty, tz
    return m


def det3(m: Matrix) -> float:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def normal_matrix(m: Matrix) -> list[list[float]]:
    """Inverse transpose of the upper 3x3, for normals."""
    d = det3(m)
    cof = [[0.0] * 3 for _ in range(3)]
    for r in range(3):
        for c in range(3):
            rows = [i for i in range(3) if i != r]
            cols = [j for j in range(3) if j != c]
            minor = m[rows[0]][cols[0]] * m[rows[1]][cols[1]] - m[rows[0]][cols[1]] * m[rows[1]][cols[0]]
            cof[r][c] = ((-1) ** (r + c)) * minor / d
    return cof  # cofactor / det == inverse transpose


def yaw_to_plus_z(source_forward: str) -> Matrix:
    """Rotation about +Y that turns the source nose direction into +Z."""
    angle = {"+z": 0.0, "+x": -90.0, "-x": 90.0, "-z": 180.0}[source_forward]
    a = math.radians(angle)
    c, s = round(math.cos(a), 12), round(math.sin(a), 12)
    m = identity()
    m[0][0], m[0][2], m[2][0], m[2][2] = c, s, -s, c
    return m


# ---- scene inspection ---------------------------------------------------------------------

def world_matrices(gltf: GLTF2) -> dict[int, Matrix]:
    scene = gltf.scenes[gltf.scene or 0]
    result: dict[int, Matrix] = {}

    def visit(index: int, parent: Matrix) -> None:
        world = matmul(parent, node_matrix(gltf.nodes[index]))
        result[index] = world
        for child in gltf.nodes[index].children or []:
            visit(child, world)

    for root in scene.nodes or []:
        visit(root, identity())
    return result


def bounds(gltf: GLTF2, worlds: dict[int, Matrix], extra: Matrix | None = None):
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for index, world in worlds.items():
        node = gltf.nodes[index]
        if node.mesh is None:
            continue
        m = matmul(extra, world) if extra else world
        for prim in gltf.meshes[node.mesh].primitives:
            acc = gltf.accessors[prim.attributes.POSITION]
            for x in (acc.min[0], acc.max[0]):
                for y in (acc.min[1], acc.max[1]):
                    for z in (acc.min[2], acc.max[2]):
                        p = apply_point(m, (x, y, z))
                        for i in range(3):
                            lo[i] = min(lo[i], p[i])
                            hi[i] = max(hi[i], p[i])
    return lo, hi


def triangle_count(gltf: GLTF2, worlds: dict[int, Matrix]) -> int:
    total = 0
    for index in worlds:
        node = gltf.nodes[index]
        if node.mesh is None:
            continue
        for prim in gltf.meshes[node.mesh].primitives:
            if prim.mode not in (None, TRIANGLES):
                continue
            if prim.indices is not None:
                total += gltf.accessors[prim.indices].count // 3
            else:
                total += gltf.accessors[prim.attributes.POSITION].count // 3
    return total


# ---- baking ---------------------------------------------------------------------------------

def _bake_blockers(gltf: GLTF2, worlds: dict[int, Matrix]) -> list[str]:
    blockers = []
    if len(gltf.buffers) != 1 or gltf.buffers[0].uri:
        blockers.append("not a single embedded GLB buffer")
    if gltf.skins:
        blockers.append("skinned mesh")
    mesh_users: dict[int, int] = {}
    owner: dict[int, int] = {}
    for index, world in worlds.items():
        node = gltf.nodes[index]
        if node.mesh is None:
            continue
        mesh_users[node.mesh] = mesh_users.get(node.mesh, 0) + 1
        if det3(world) <= 0:
            blockers.append(f"node {index} has a mirroring transform")
        for prim in gltf.meshes[node.mesh].primitives:
            if prim.targets:
                blockers.append("morph targets")
            for name in ("POSITION", "NORMAL", "TANGENT"):
                acc_index = getattr(prim.attributes, name)
                if acc_index is None:
                    continue
                acc = gltf.accessors[acc_index]
                if acc.sparse or acc.componentType != FLOAT or acc.bufferView is None:
                    blockers.append(f"{name} accessor {acc_index} is not plain float data")
                if owner.setdefault(acc_index, index) != index:
                    blockers.append(f"accessor {acc_index} shared between nodes")
    blockers.extend(f"mesh {m} used by {n} nodes" for m, n in mesh_users.items() if n > 1)
    return sorted(set(blockers))


def _rewrite(gltf: GLTF2, blob: bytearray, acc_index: int, fn, size: int) -> None:
    acc = gltf.accessors[acc_index]
    view = gltf.bufferViews[acc.bufferView]
    start = (view.byteOffset or 0) + (acc.byteOffset or 0)
    stride = view.byteStride or size * 4
    fmt = f"<{size}f"
    lo = [math.inf] * min(size, 3)
    hi = [-math.inf] * min(size, 3)
    for i in range(acc.count):
        offset = start + i * stride
        values = fn(struct.unpack_from(fmt, blob, offset))
        struct.pack_into(fmt, blob, offset, *values)
        for k in range(len(lo)):
            lo[k] = min(lo[k], values[k])
            hi[k] = max(hi[k], values[k])
    if acc.min is not None or fn.__name__ == "pos":
        acc.min, acc.max = lo, hi


def _bake(gltf: GLTF2, worlds: dict[int, Matrix], transform: Matrix) -> None:
    blob = bytearray(gltf.binary_blob())
    done: set[int] = set()
    for index, world in worlds.items():
        node = gltf.nodes[index]
        if node.mesh is None:
            continue
        full = matmul(transform, world)
        nmat = normal_matrix(full)

        def pos(v):
            return apply_point(full, v)

        def nrm(v):
            x = [sum(nmat[r][c] * v[c] for c in range(3)) for r in range(3)]
            length = math.sqrt(sum(c * c for c in x)) or 1.0
            return tuple(c / length for c in x)

        def tan(v):
            x = [sum(full[r][c] * v[c] for c in range(3)) for r in range(3)]
            length = math.sqrt(sum(c * c for c in x)) or 1.0
            return (*[c / length for c in x], v[3])

        for prim in gltf.meshes[node.mesh].primitives:
            for name, fn, size in (("POSITION", pos, 3), ("NORMAL", nrm, 3), ("TANGENT", tan, 4)):
                acc_index = getattr(prim.attributes, name)
                if acc_index is None or acc_index in done:
                    continue
                _rewrite(gltf, blob, acc_index, fn, size)
                done.add(acc_index)
    for node in gltf.nodes:
        node.matrix = None
        node.translation = node.rotation = node.scale = None
    gltf.set_binary_blob(bytes(blob))


# ---- textures --------------------------------------------------------------------------------

def _image_bytes(gltf: GLTF2, image_index: int) -> tuple[bytes, str]:
    image = gltf.images[image_index]
    if image.bufferView is not None:
        view = gltf.bufferViews[image.bufferView]
        start = view.byteOffset or 0
        data = gltf.binary_blob()[start:start + view.byteLength]
        return data, image.mimeType or "image/png"
    if image.uri and image.uri.startswith("data:"):
        header, payload = image.uri.split(",", 1)
        return base64.b64decode(payload), header[5:].split(";")[0]
    raise ValueError(f"image {image_index} is an external file reference")


def extract_textures(gltf: GLTF2, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    materials = []
    for m_index, material in enumerate(gltf.materials or []):
        pbr = material.pbrMetallicRoughness
        slots = {
            "base_color": pbr.baseColorTexture if pbr else None,
            "metallic_roughness": pbr.metallicRoughnessTexture if pbr else None,
            "normal": material.normalTexture,
            "occlusion": material.occlusionTexture,
            "emissive": material.emissiveTexture,
        }
        entry = {
            "material": m_index, "name": material.name, "textures": {},
            "base_color_factor": pbr.baseColorFactor if pbr else None,
            "metallic_factor": pbr.metallicFactor if pbr else None,
            "roughness_factor": pbr.roughnessFactor if pbr else None,
            "emissive_factor": material.emissiveFactor,
        }
        sources = {}
        for role, info in slots.items():
            if info is None:
                continue
            source = gltf.textures[info.index].source
            sources[role] = source
            data, mime = _image_bytes(gltf, source)
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".bin")
            name = f"m{m_index}_{role}{ext}"
            (out_dir / name).write_bytes(data)
            entry["textures"][role] = name
            if role == "normal":
                # glTF normal maps are OpenGL (+Y). Unreal's fleet material expects DirectX (-Y).
                with Image.open(io.BytesIO(data)) as img:
                    r, g, b, *rest = img.convert("RGBA").split()
                    g = g.point(lambda v: 255 - v)
                    Image.merge("RGB", (r, g, b)).save(out_dir / f"m{m_index}_normal_dx.png")
                entry["textures"]["normal_dx"] = f"m{m_index}_normal_dx.png"
        entry["packed_orm"] = (
            "occlusion" in sources and sources.get("occlusion") == sources.get("metallic_roughness")
        )
        entry["channels"] = ("metallic_roughness: G = roughness, B = metallic"
                             + (", R = occlusion (packed ORM)" if entry["packed_orm"] else ", R unused"))
        materials.append(entry)
    return materials


# ---- entry point -------------------------------------------------------------------------------

def normalize(src: Path, dst: Path, *, length_m: float, source_forward: str,
              textures_dir: Path | None = None, expected_width_m: float | None = None,
              expected_height_m: float | None = None, face_limit: int | None = None) -> dict:
    gltf = GLTF2().load(str(src))
    worlds = world_matrices(gltf)
    src_lo, src_hi = bounds(gltf, worlds)
    src_size = [h - l for l, h in zip(src_lo, src_hi)]
    warnings = []

    forward_axis = 0 if source_forward.endswith("x") else 2
    side_axis = 2 if forward_axis == 0 else 0
    if src_size[side_axis] > src_size[forward_axis] * 1.05:
        warnings.append(
            f"the model is longer along {'XZ'[side_axis // 2]} than along the configured forward axis "
            f"{source_forward}; source_forward is probably wrong for this generator"
        )

    rot = yaw_to_plus_z(source_forward)
    center = [(l + h) / 2 for l, h in zip(src_lo, src_hi)]
    shift = identity()
    shift[0][3], shift[1][3], shift[2][3] = (-c for c in center)
    rotated_lo, rotated_hi = bounds(gltf, worlds, matmul(rot, shift))
    length_now = rotated_hi[2] - rotated_lo[2]
    scale = length_m / length_now
    scale_m = identity()
    for i in range(3):
        scale_m[i][i] = scale
    transform = matmul(scale_m, matmul(rot, shift))

    blockers = _bake_blockers(gltf, worlds)
    if blockers:
        method = "root-node"
        warnings.append("not baked: " + "; ".join(blockers))
        scene = gltf.scenes[gltf.scene or 0]
        gltf.nodes.append(Node(name="fleet_normalize", matrix=to_column_major(transform),
                               children=list(scene.nodes or [])))
        scene.nodes = [len(gltf.nodes) - 1]
    else:
        method = "baked"
        _bake(gltf, worlds, transform)

    final_worlds = world_matrices(gltf)
    lo, hi = bounds(gltf, final_worlds)
    width, height, length = (hi[i] - lo[i] for i in range(3))
    triangles = triangle_count(gltf, final_worlds)
    if face_limit and triangles > face_limit:
        warnings.append(f"{triangles} triangles exceed face_limit {face_limit}")
    if len(gltf.materials or []) != 1:
        warnings.append(f"{len(gltf.materials or [])} materials; the fleet catalog expects one slot")

    dst.parent.mkdir(parents=True, exist_ok=True)
    gltf.save_binary(str(dst))
    materials = extract_textures(gltf, textures_dir) if textures_dir else []
    if textures_dir:
        (textures_dir / "material.json").write_text(json.dumps(materials, indent=2) + "\n", encoding="utf-8")

    return {
        "method": method,
        "source_forward": source_forward,
        "uniform_scale": scale,
        "source_bounds_size": src_size,
        "result_m": {"length": length, "width": width, "height": height},
        "expected_m": {"length": length_m, "width": expected_width_m, "height": expected_height_m},
        "result_center": [(l + h) / 2 for l, h in zip(lo, hi)],
        "triangles": triangles,
        "materials": len(gltf.materials or []),
        "warnings": warnings,
    }
