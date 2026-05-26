"""Regression test for BL-MAT-001:linked-basecolor-flattens-previewsurface.

Stock Blender's `usd_writer_material.cc::traverse_channel` did not cross
`ShaderNodeGroup` boundaries. When a top-level Principled BSDF had inputs
wired through a Group node wrapping the texture chain (mikassa's standard
shading pattern: `Principled.Base Color ← Group.004.Color → (inside)
ShaderNodeTexImage`), the channel walker fell through to the constant-value
branch and the exported `UsdPreviewSurface` emitted `inputs:diffuseColor =
(r, g, b)` instead of `inputs:diffuseColor.connect = .../<texture>.outputs:rgb`.
The same applied to `inputs:normal`, dropping the UDIM normal map entirely.

This test drives the real AYON `mikassa-v001.blend` asset through
`bpy.ops.wm.usd_export` and asserts via `pxr.UsdShade` that the patched
exporter:

  * connects `inputs:diffuseColor` on the Principled_BSDF shader of the
    `mika_trousers` Material to a `UsdUVTexture` shader (not a literal RGB),
  * connects `inputs:normal` to a second `UsdUVTexture`,
  * authors a UDIM `<UDIM>` token in the texture `inputs:file` asset path
    (proving the UDIM source was carried through the group descent), and
  * authors `inputs:scale = (2,2,2,2)` / `inputs:bias = (-1,-1,-1,-1)` on the
    normal-map texture (the standard tangent-space decode that the existing
    normal-texture-range path applies once the texture node is actually
    reachable).

If a future refactor stops descending into `ShaderNodeGroup`s during the
upstream walk, these assertions fail with a clear pointer at the regression.

Run via:
    /path/to/patched/Blender --background \\
        <mikassa-v001.blend> --python <this-file> -- \\
        --output-usd /tmp/mikassa_bl_mat_001.usda
"""

import argparse
import os
import sys

import bpy

MATERIAL_NAME = "mika-trousers"
SANITIZED_NAME = "mika_trousers"


def parse_args() -> argparse.Namespace:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]
    else:
        args = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-usd", required=True)
    return parser.parse_args(args)


def select_object_using(material_name: str) -> None:
    """Select (and un-hide) the first MESH whose slots reference material_name."""
    target = None
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if slot.material is not None and slot.material.name == material_name:
                target = obj
                break
        if target:
            break
    if target is None:
        raise RuntimeError(
            f"No mesh in scene uses material {material_name!r} — "
            f"this test needs the real AYON mikassa scene."
        )

    bpy.ops.object.select_all(action="DESELECT")
    target.hide_set(False)
    target.hide_viewport = False
    target.hide_render = False
    target.select_set(True)
    bpy.context.view_layer.objects.active = target


def main() -> None:
    cli = parse_args()
    out_usd = os.path.abspath(cli.output_usd)
    os.makedirs(os.path.dirname(out_usd), exist_ok=True)

    select_object_using(MATERIAL_NAME)

    print(f"[bl-mat-001-linked-basecolor] exporting USD -> {out_usd}")
    result = bpy.ops.wm.usd_export(
        filepath=out_usd,
        selected_objects_only=True,
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=False,
    )
    assert "FINISHED" in result, f"usd_export did not finish: {result}"

    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"failed to open exported stage {out_usd}"

    target_mat = None
    for prim in stage.Traverse():
        if prim.GetTypeName() == "Material" and prim.GetName() == SANITIZED_NAME:
            target_mat = prim
            break
    all_materials = [p.GetName() for p in stage.Traverse() if p.GetTypeName() == "Material"]
    assert target_mat is not None, (
        f"Material {SANITIZED_NAME!r} not found in exported stage. "
        f"All materials: {all_materials}"
    )
    print(f"[bl-mat-001-linked-basecolor] target material at {target_mat.GetPath().pathString}")

    # Locate the UsdPreviewSurface shader (Principled_BSDF) under the material.
    preview_surface = None
    for child in target_mat.GetChildren():
        if child.GetTypeName() != "Shader":
            continue
        sh = UsdShade.Shader(child)
        if sh.GetIdAttr().Get() == "UsdPreviewSurface":
            preview_surface = sh
            break
    assert preview_surface is not None, (
        "Regression: no UsdPreviewSurface shader found under mika_trousers."
    )

    def assert_connected_to_uvtexture(input_name: str, expected_asset_substring: str):
        sock = preview_surface.GetInput(input_name)
        assert sock is not None, f"Principled BSDF missing input {input_name!r}"
        assert sock.HasConnectedSource(), (
            f"Regression (BL-MAT-001:linked-basecolor-flattens-previewsurface): "
            f"Principled_BSDF.{input_name} on {SANITIZED_NAME} has no connection. "
            f"The exporter flattened a linked socket to a constant — "
            f"traverse_channel did not descend into the ShaderNodeGroup wrapper."
        )
        api, src_name, _ = sock.GetConnectedSource()
        src_prim = api.GetPrim()
        assert src_prim.GetTypeName() == "Shader", (
            f"Principled_BSDF.{input_name} connected to non-Shader prim "
            f"{src_prim.GetPath().pathString} (type {src_prim.GetTypeName()!r})"
        )
        src_shader = UsdShade.Shader(src_prim)
        src_id = src_shader.GetIdAttr().Get()
        assert src_id == "UsdUVTexture", (
            f"Principled_BSDF.{input_name} connected to non-UsdUVTexture shader "
            f"(id {src_id!r}); expected a texture node reached through the group descent."
        )
        file_input = src_shader.GetInput("file")
        assert file_input is not None, (
            f"UsdUVTexture feeding {input_name} has no inputs:file"
        )
        asset = file_input.Get()
        assert asset is not None, (
            f"UsdUVTexture feeding {input_name} has unauthored file path"
        )
        # asset is an SdfAssetPath
        path = getattr(asset, "path", None) or str(asset)
        assert expected_asset_substring in path, (
            f"UsdUVTexture feeding {input_name} points to {path!r}; "
            f"expected substring {expected_asset_substring!r}. The wrong "
            f"texture was wired up (or the UDIM token was stripped)."
        )
        return src_shader, path

    diff_shader, diff_path = assert_connected_to_uvtexture(
        "diffuseColor", "mikassa-base_color"
    )
    norm_shader, norm_path = assert_connected_to_uvtexture(
        "normal", "mikassa-painterly_normals"
    )

    # UDIM tokens must survive the group descent.
    assert "<UDIM>" in diff_path, (
        f"diffuseColor texture path {diff_path!r} lost its <UDIM> token — "
        f"the inner ShaderNodeTexImage's tiled-source state was not "
        f"preserved when crossing the ShaderNodeGroup boundary."
    )
    assert "<UDIM>" in norm_path, (
        f"normal texture path {norm_path!r} lost its <UDIM> token."
    )

    # Tangent-space normal decode (scale 2, bias -1) must be authored on the
    # normal texture. This only fires once the inner image-texture node is
    # actually reachable via the patched traversal.
    scale_input = norm_shader.GetInput("scale")
    bias_input = norm_shader.GetInput("bias")
    assert scale_input is not None and scale_input.Get() is not None, (
        "Normal-map UsdUVTexture is missing inputs:scale — the "
        "normal-texture-range path didn't fire because the texture node "
        "wasn't reached."
    )
    assert bias_input is not None and bias_input.Get() is not None, (
        "Normal-map UsdUVTexture is missing inputs:bias."
    )
    scale = tuple(scale_input.Get())
    bias = tuple(bias_input.Get())
    assert scale[:3] == (2.0, 2.0, 2.0), (
        f"Normal map scale {scale!r} != (2,2,2,_); standard tangent-space "
        f"decode not applied."
    )
    assert bias[:3] == (-1.0, -1.0, -1.0), (
        f"Normal map bias {bias!r} != (-1,-1,-1,_); standard tangent-space "
        f"decode not applied."
    )

    # The UV reader feeding both textures must be authored as well (proving
    # the inner ShaderNodeUVMap node was reached too).
    for label, tex in (("diffuse", diff_shader), ("normal", norm_shader)):
        st_input = tex.GetInput("st")
        assert st_input is not None and st_input.HasConnectedSource(), (
            f"UsdUVTexture for {label} has no st connection; the UV reader "
            f"inside the ShaderNodeGroup wasn't reached."
        )
        api, _, _ = st_input.GetConnectedSource()
        uv_shader = UsdShade.Shader(api.GetPrim())
        assert uv_shader.GetIdAttr().Get() == "UsdPrimvarReader_float2", (
            f"st connection for {label} doesn't reach a UsdPrimvarReader_float2"
        )

    print(f"[bl-mat-001-linked-basecolor] EXPORTED_USD_PATH={out_usd}")
    print("[bl-mat-001-linked-basecolor] PASS")


if __name__ == "__main__":
    main()
