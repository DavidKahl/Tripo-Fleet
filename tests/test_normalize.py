import io
import json
import struct

import pytest
from PIL import Image
from pygltflib import (GLTF2, Accessor, Asset, Attributes, Buffer, BufferView, Image as GImage, Material,
                       Mesh, Node, NormalMaterialTexture, PbrMetallicRoughness, Primitive, Scene, Texture,
                       TextureInfo)

from tripo_fleet import gltf_norm

# A box 4 long (X), 1 high (Y), 2 wide (Z): a vehicle whose nose points +X
CORNERS = [(x, y, z) for x in (0.0, 4.0) for y in (0.0, 1.0) for z in (0.0, 2.0)]


def make_glb(path, node_kwargs=None, with_texture=False):
    positions = b"".join(struct.pack("<3f", *p) for p in CORNERS)
    normals = b"".join(struct.pack("<3f", 1.0, 0.0, 0.0) for _ in CORNERS)
    tangents = b"".join(struct.pack("<4f", 0.0, 0.0, 1.0, 1.0) for _ in CORNERS)
    indices = struct.pack("<36H", *([0, 1, 2] * 12))
    blob = positions + normals + tangents + indices
    views = [
        BufferView(buffer=0, byteOffset=0, byteLength=len(positions)),
        BufferView(buffer=0, byteOffset=len(positions), byteLength=len(normals)),
        BufferView(buffer=0, byteOffset=len(positions) + len(normals), byteLength=len(tangents)),
        BufferView(buffer=0, byteOffset=len(positions) + len(normals) + len(tangents), byteLength=len(indices)),
    ]
    materials, textures, images = [], [], []
    if with_texture:
        png = io.BytesIO()
        Image.new("RGB", (4, 4), (128, 200, 255)).save(png, format="PNG")
        pad = (-len(blob)) % 4
        blob += b"\0" * pad
        views.append(BufferView(buffer=0, byteOffset=len(blob), byteLength=len(png.getvalue())))
        blob += png.getvalue()
        images = [GImage(bufferView=4, mimeType="image/png")]
        textures = [Texture(source=0)]
        materials = [Material(pbrMetallicRoughness=PbrMetallicRoughness(baseColorTexture=TextureInfo(index=0)),
                              normalTexture=NormalMaterialTexture(index=0))]
    gltf = GLTF2(
        asset=Asset(version="2.0"), scene=0, scenes=[Scene(nodes=[0])],
        nodes=[Node(mesh=0, **(node_kwargs or {}))],
        meshes=[Mesh(primitives=[Primitive(attributes=Attributes(POSITION=0, NORMAL=1, TANGENT=2), indices=3,
                                           material=0 if with_texture else None)])],
        accessors=[
            Accessor(bufferView=0, componentType=5126, count=8, type="VEC3", min=[0, 0, 0], max=[4, 1, 2]),
            Accessor(bufferView=1, componentType=5126, count=8, type="VEC3"),
            Accessor(bufferView=2, componentType=5126, count=8, type="VEC4"),
            Accessor(bufferView=3, componentType=5123, count=36, type="SCALAR"),
        ],
        bufferViews=views, buffers=[Buffer(byteLength=len(blob))],
        materials=materials, textures=textures, images=images,
    )
    gltf.set_binary_blob(blob)
    gltf.save_binary(str(path))


def read_vec(gltf, accessor, size):
    acc = gltf.accessors[accessor]
    view = gltf.bufferViews[acc.bufferView]
    start = (view.byteOffset or 0) + (acc.byteOffset or 0)
    return struct.unpack_from(f"<{size}f", gltf.binary_blob(), start)


def test_bake_rotates_scales_and_centres(tmp_path):
    make_glb(tmp_path / "raw.glb", {"translation": [10.0, 5.0, -3.0]})
    result = gltf_norm.normalize(tmp_path / "raw.glb", tmp_path / "out.glb", length_m=6.0, source_forward="+x")
    assert result["method"] == "baked"
    assert result["result_m"]["length"] == pytest.approx(6.0)
    assert result["result_m"]["width"] == pytest.approx(3.0)
    assert result["result_m"]["height"] == pytest.approx(1.5)
    assert result["result_center"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert result["triangles"] == 12
    assert not [w for w in result["warnings"] if "forward" in w]

    out = GLTF2().load(str(tmp_path / "out.glb"))
    assert out.nodes[0].translation is None and out.nodes[0].matrix is None
    assert read_vec(out, 1, 3) == pytest.approx((0.0, 0.0, 1.0), abs=1e-6)  # nose normal now +Z
    assert read_vec(out, 2, 4)[3] == 1.0


def test_wrong_forward_axis_is_flagged(tmp_path):
    make_glb(tmp_path / "raw.glb")
    result = gltf_norm.normalize(tmp_path / "raw.glb", tmp_path / "out.glb", length_m=6.0, source_forward="+z")
    assert any("forward" in w for w in result["warnings"])


def test_mirrored_node_falls_back_to_root_node(tmp_path):
    make_glb(tmp_path / "raw.glb", {"scale": [1.0, 1.0, -1.0]})
    result = gltf_norm.normalize(tmp_path / "raw.glb", tmp_path / "out.glb", length_m=6.0, source_forward="+x")
    assert result["method"] == "root-node"
    assert result["result_m"]["length"] == pytest.approx(6.0)
    out = GLTF2().load(str(tmp_path / "out.glb"))
    assert out.nodes[out.scenes[0].nodes[0]].name == "fleet_normalize"


def test_textures_extracted_with_dx_normal(tmp_path):
    make_glb(tmp_path / "raw.glb", with_texture=True)
    gltf_norm.normalize(tmp_path / "raw.glb", tmp_path / "out.glb", length_m=6.0, source_forward="+x",
                        textures_dir=tmp_path / "tex")
    material = json.loads((tmp_path / "tex" / "material.json").read_text())[0]
    assert material["textures"]["base_color"] == "m0_base_color.png"
    with Image.open(tmp_path / "tex" / material["textures"]["normal_dx"]) as img:
        assert img.getpixel((0, 0)) == (128, 55, 255)
