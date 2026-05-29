"""nd-normalmap-float-nodedef-missing — MaterialX normalmap nodedef name compatibility.

Blender's MaterialX writer emits a `<normalmap>` element with `scale: float`
(from Bump.distance / NormalMap.strength). When `UsdMtlxRead` resolves that
against the bundled MaterialX 1.39 stdlib it sets the Shader's `info:id` to
`ND_normalmap_float` (the v1.39-suffixed name; the suffix is the *scale*
input's type). Downstream consumers still on a MaterialX 1.38 stdlib
(e.g. older Omniverse USD builds, older Hydra Storm) only know the legacy
un-suffixed `ND_normalmap` nodedef and fail to resolve the suffixed name —
on Omniverse this hits the NodeDef lookup in hdMtlx and brings the import
down for every Singularity asset that touches a normal map.

OpenUSD's HdMtlx ships a backward-compat shim (`HdMtlxGetNodeDefName`) that
remaps `ND_normalmap` -> `ND_normalmap_float` for 1.39+ consumers, so
authoring the legacy `ND_normalmap` name keeps us correct in both
directions: 1.38 consumers resolve it directly, 1.39+ consumers go through
the shim. The patched exporter normalizes `ND_normalmap_float` /
`ND_normalmap_vector2` -> `ND_normalmap` in the post-`UsdMtlxRead` pass.

This test drives the real AYON `mikassa-v001.blend` hero (40 Bump/NormalMap
chains across its painterly subgraph) with both `generate_preview_surface=True`
and `generate_materialx_network=True` and asserts the exported USD contains
zero `ND_normalmap_float` / `ND_normalmap_vector2` shaders (regression
sentinel: stock Blender authors 40 of them).

Run:
  <patched-blender> --background <mikassa-v001.blend> --python this_script.py
"""
import bpy
import os
import sys
import tempfile

from pxr import Usd, UsdShade

BLEND = bpy.data.filepath
OUT = os.path.join(tempfile.gettempdir(), "test_nd_normalmap_float_mikassa_mtlx.usda")


def export():
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)
    bpy.ops.wm.usd_export(
        filepath=OUT,
        check_existing=False,
        selected_objects_only=False,
        evaluation_mode="RENDER",
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=True,
        convert_world_material=True,
        root_prim_path="/root",
    )
    print(f"USD_EXPORT_PATH={OUT}")


def validate():
    stage = Usd.Stage.Open(OUT)

    suffixed = []
    legacy = []
    bad_in_type = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader":
            continue
        shader = UsdShade.Shader(prim)
        id_attr = shader.GetIdAttr()
        tok = id_attr.Get() if id_attr else None
        if tok in ("ND_normalmap_float", "ND_normalmap_vector2"):
            suffixed.append((prim.GetPath().pathString, tok))
        elif tok == "ND_normalmap":
            legacy.append(prim.GetPath().pathString)
            # The legacy nodedef declares `in` as vector3. Any upstream
            # connection must therefore be float3-typed or the rewritten
            # network would be ill-typed.
            in_input = shader.GetInput("in")
            if in_input and in_input.HasConnectedSource():
                src_api, src_name, _ = in_input.GetConnectedSource()
                src_shader = UsdShade.Shader(src_api.GetPrim())
                src_output = src_shader.GetOutput(src_name)
                src_type = str(src_output.GetTypeName()) if src_output else None
                if src_type not in ("float3",):
                    bad_in_type.append((prim.GetPath().pathString, src_type))

    assert len(suffixed) == 0, (
        f"Expected ZERO ND_normalmap_float / ND_normalmap_vector2 shaders post-fix, "
        f"got {len(suffixed)}: {suffixed[:5]}"
    )
    # mikassa-v001 has roughly 40 NormalMap/Bump chains feeding the MaterialX
    # painterly surface for the hero pass. Don't pin the exact integer (geometry
    # changes shouldn't break the test) but require a non-trivial set so the
    # sentinel actually exercises the path on this asset.
    assert len(legacy) >= 10, (
        f"Expected at least 10 ND_normalmap shaders on mikassa MaterialX export, "
        f"got {len(legacy)}"
    )
    assert len(bad_in_type) == 0, (
        f"ND_normalmap.in must be float3-typed; got mismatches: {bad_in_type[:5]}"
    )
    print(
        f"OK ND_normalmap_float={0} ND_normalmap_vector2={0} "
        f"ND_normalmap={len(legacy)} bad_in_type=0"
    )


if __name__ == "__main__":
    if not BLEND or "mikassa" not in BLEND.lower():
        sys.stderr.write(
            "Skipping: this test must be driven against mikassa-v001.blend\n"
        )
        sys.exit(0)
    export()
    validate()
