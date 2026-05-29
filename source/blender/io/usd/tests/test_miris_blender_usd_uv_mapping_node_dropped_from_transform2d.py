"""
Test: blender-usd-uv-mapping-node-dropped-from-transform2d.

The Cycles ShaderNodeMapping node sits between TexCoord/UVMap and the
Image Texture in nearly every authored Blender material that tiles or
rotates UVs. Stock Blender's USD exporter emits a UsdTransform2d for the
node only when its `vector_type == POINT`; for the other three modes
(TEXTURE, VECTOR, NORMAL) it silently traverses past, leaving
`UsdUVTexture.st` wired directly to the `UsdPrimvarReader_float2`. The
artist's authored scale/rotation/translation is dropped entirely.

This test opens a real production .blend from the AYON corpus
(mikassa-v001 — chosen because it lives under
`Project Gold Files/220_0020-packed/assets/chars/`, the canonical
hero-asset slot), appends a single test sphere carrying a Mapping-driven
material for each of the four `vector_type` modes, exports via
`bpy.ops.wm.usd_export`, and asserts via `pxr.UsdShade` that each
material's exported UsdUVTexture.st is connected through a
UsdTransform2d shader carrying the authored parameters (POINT exact;
TEXTURE inverted per the Cycles semantic; VECTOR/NORMAL with translation
dropped per Cycles' geometric meaning).

Drive with the PATCHED build:
    .../build_darwin/bin/Blender.app/Contents/MacOS/Blender \
        --background \
        --python test_miris_blender_usd_uv_mapping_node_dropped_from_transform2d.py
"""
import math
import os
import sys

import bpy

CORPUS_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Project Gold Files/220_0020-packed/"
    "assets/chars/mikassa/publish/mikassa-v001.blend"
)


def _make_checker_image(name: str, size: int = 64) -> bpy.types.Image:
    img = bpy.data.images.new(name, width=size, height=size, alpha=False)
    pixels = []
    cell = max(1, size // 8)
    for y in range(size):
        for x in range(size):
            c = ((x // cell) + (y // cell)) % 2
            pixels.extend([1.0, 0.2, 0.2, 1.0] if c == 0 else [1.0, 1.0, 1.0, 1.0])
    img.pixels = pixels
    img.update()
    img.pack()
    return img


def _make_mapping_material(name: str, vector_type: str, scale_xy: float,
                           rot_z_rad: float, loc_x: float, loc_y: float,
                           img: bpy.types.Image) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.location = (0, 0)
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-300, 0)
    mapping.vector_type = vector_type
    if "Scale" in mapping.inputs:
        mapping.inputs["Scale"].default_value = (scale_xy, scale_xy, 1.0)
    if "Rotation" in mapping.inputs:
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, rot_z_rad)
    if "Location" in mapping.inputs:
        mapping.inputs["Location"].default_value = (loc_x, loc_y, 0.0)
    texcoord = nt.nodes.new("ShaderNodeTexCoord")
    texcoord.location = (-600, 0)
    nt.links.new(texcoord.outputs["UV"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _open_corpus() -> None:
    if not bpy.data.filepath or "mikassa-v001" not in bpy.data.filepath:
        if not os.path.isfile(CORPUS_BLEND):
            raise FileNotFoundError(f"mikassa-v001.blend missing at {CORPUS_BLEND}")
        bpy.ops.wm.open_mainfile(filepath=CORPUS_BLEND)


def _inject_test_geometry(img: bpy.types.Image) -> list[tuple[str, str, dict]]:
    """Add 4 test spheres into the open production scene, one per vector_type.

    Returns a list of (object_name, material_name, authored_params) tuples.
    """
    cases = [
        ("mapping_point", "POINT", 4.0, math.radians(30.0), 0.1, 0.2),
        ("mapping_texture", "TEXTURE", 2.0, math.radians(15.0), 0.05, 0.10),
        ("mapping_vector", "VECTOR", 3.0, math.radians(45.0), 0.3, 0.4),
        ("mapping_normal", "NORMAL", 2.5, math.radians(60.0), 0.0, 0.0),
    ]
    out = []
    for i, (obj_name, vt, sx, rz, tx, ty) in enumerate(cases):
        bpy.ops.mesh.primitive_uv_sphere_add(location=(i * 3.0, 100.0, 0.0))
        ob = bpy.context.active_object
        ob.name = obj_name
        mat_name = f"M_test_{obj_name}"
        mat = _make_mapping_material(mat_name, vt, sx, rz, tx, ty, img)
        ob.data.materials.clear()
        ob.data.materials.append(mat)
        out.append((obj_name, mat_name,
                    {"vector_type": vt, "scale_xy": sx, "rot_z_rad": rz,
                     "loc_x": tx, "loc_y": ty}))
    return out


def _assert_transform2d(stage, mat_name: str, params: dict) -> None:
    from pxr import Sdf, UsdShade, Gf
    mat_path = f"/root/_materials/{mat_name}"
    mat_prim = stage.GetPrimAtPath(mat_path)
    assert mat_prim, f"material prim missing: {mat_path}"
    # Find the UsdUVTexture child.
    uv_tex = None
    transform2d = None
    primvar_reader = None
    for child in mat_prim.GetChildren():
        if not child.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(child)
        info_id = shader.GetIdAttr().Get()
        if info_id == "UsdUVTexture":
            uv_tex = shader
        elif info_id == "UsdTransform2d":
            transform2d = shader
        elif info_id == "UsdPrimvarReader_float2":
            primvar_reader = shader
    assert uv_tex is not None, f"UsdUVTexture not found under {mat_path}"
    assert transform2d is not None, (
        f"UsdTransform2d missing under {mat_path} — "
        f"vector_type={params['vector_type']} should emit one")
    assert primvar_reader is not None, f"UsdPrimvarReader_float2 missing under {mat_path}"

    # UsdUVTexture.st must connect to UsdTransform2d.outputs:result, NOT
    # directly to the primvar reader.
    st_input = uv_tex.GetInput("st")
    assert st_input, f"UsdUVTexture.st missing on {mat_path}"
    sources = st_input.GetConnectedSources()[0]
    assert sources, f"UsdUVTexture.st not connected on {mat_path}"
    src_api, src_name = sources[0].source, sources[0].sourceName
    assert src_api.GetPath() == transform2d.GetPath() and src_name == "result", (
        f"{mat_path}: UsdUVTexture.st should connect to "
        f"{transform2d.GetPath()}.outputs:result; got "
        f"{src_api.GetPath()}.outputs:{src_name}")

    # UsdTransform2d.in must connect to UsdPrimvarReader_float2.outputs:result.
    in_input = transform2d.GetInput("in")
    assert in_input, f"UsdTransform2d.in missing on {mat_path}"
    in_sources = in_input.GetConnectedSources()[0]
    assert in_sources, f"UsdTransform2d.in not connected on {mat_path}"

    # Verify per-vector-type parameter authoring.
    scale = transform2d.GetInput("scale").Get()
    rotation = transform2d.GetInput("rotation").Get()
    translation = transform2d.GetInput("translation").Get()
    sx, rz, tx, ty = (params["scale_xy"], params["rot_z_rad"],
                      params["loc_x"], params["loc_y"])
    vt = params["vector_type"]

    def close(a, b, tol=1e-4):
        return abs(a - b) <= tol

    if vt == "POINT":
        exp_scale = (sx, sx)
        exp_rot_deg = math.degrees(rz)
        exp_trans = (tx, ty)
    elif vt == "TEXTURE":
        # Cycles TEXTURE inverts the transform: scale = 1/S, rot = -rot,
        # translation = -(inv(S) * R(-rot) * T).
        inv_sx = 1.0 / sx
        c = math.cos(-rz)
        s = math.sin(-rz)
        rx = c * tx - s * ty
        ry = s * tx + c * ty
        exp_scale = (inv_sx, inv_sx)
        exp_rot_deg = math.degrees(-rz)
        exp_trans = (-inv_sx * rx, -inv_sx * ry)
    elif vt in ("VECTOR", "NORMAL"):
        # Cycles VECTOR/NORMAL drop the translation.
        exp_scale = (sx, sx)
        exp_rot_deg = math.degrees(rz)
        exp_trans = (0.0, 0.0)
    else:
        raise AssertionError(f"unknown vector_type {vt}")

    assert close(scale[0], exp_scale[0]) and close(scale[1], exp_scale[1]), (
        f"{mat_path}: scale {tuple(scale)} != expected {exp_scale} for {vt}")
    assert close(rotation, exp_rot_deg), (
        f"{mat_path}: rotation {rotation} != expected {exp_rot_deg} for {vt}")
    assert close(translation[0], exp_trans[0]) and close(translation[1], exp_trans[1]), (
        f"{mat_path}: translation {tuple(translation)} != expected {exp_trans} for {vt}")
    print(f"  OK {vt:8s} scale={tuple(scale)} rot={rotation:.4f}deg trans={tuple(translation)}")


def main() -> int:
    _open_corpus()
    img = _make_checker_image("uv_mapping_test_checker")
    cases = _inject_test_geometry(img)

    out_dir = "/tmp/blmap_test"
    os.makedirs(out_dir, exist_ok=True)
    usd_path = os.path.join(out_dir, "uv_mapping_corpus.usda")

    bpy.ops.wm.usd_export(
        filepath=usd_path,
        selected_objects_only=False,
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=False,
    )
    print(f"EXPORTED_USD: {usd_path}")

    from pxr import Usd
    stage = Usd.Stage.Open(usd_path)
    assert stage, f"failed to open {usd_path}"

    print("Per-material assertions:")
    for obj_name, mat_name, params in cases:
        _assert_transform2d(stage, mat_name, params)

    print("ALL ASSERTIONS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
