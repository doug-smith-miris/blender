"""
Test: bake-unrepresentable-albedo on swarmfish creature_body.

Swarmfish creature_body's Principled BSDF Base Color is fed through an
HSV / Mix / Vector-Math chain. With the prior allowlist fix (PR #25 /
commit d76b14eb / `is_signal_carrying_input`) the exporter correctly
refuses to misattribute the upstream `roughness-watercolor.exr` as
diffuse, but it has no representable path to the real diffuse texture
either, so diffuseColor falls back to the BSDF socket's default. This
test verifies that the Miris `bake_and_export` driver:

  (a) detects creature_body as having an unrepresentable Base Color,
  (b) bakes a per-material PNG next to the .usd, and
  (c) rewires the exported UsdPreviewSurface so diffuseColor connects
      to a UsdUVTexture pointing at the baked PNG, threaded through a
      UsdPrimvarReader_float2 with varname='st'.

Drive with the PATCHED build:
    .../build_darwin/bin/Blender.app/Contents/MacOS/Blender \
        --background <swarmfish-v001.blend> \
        --python test_miris_bake_unrepresentable_albedo.py
"""
import os
import sys

import bpy

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from miris_bake_unrepresentable_albedo import (  # noqa: E402
    bake_and_export,
    find_unrepresentable_materials,
)

CORPUS_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/assets/char/"
    "swarmfish/publish/swarmfish-v001.blend"
)


def _open_swarmfish() -> None:
    # `--background <file>` already loaded; if not, open it explicitly.
    if not bpy.data.filepath or "swarmfish-v001" not in bpy.data.filepath:
        if not os.path.isfile(CORPUS_BLEND):
            raise FileNotFoundError(f"swarmfish-v001.blend missing at {CORPUS_BLEND}")
        bpy.ops.wm.open_mainfile(filepath=CORPUS_BLEND)


def main() -> int:
    _open_swarmfish()

    out_dir = "/tmp/bake_unrep_albedo"
    os.makedirs(out_dir, exist_ok=True)
    usd_path = os.path.join(out_dir, "swarmfish_baked.usda")

    # Confirm the regression sentinel: stock allowlist-only export would
    # leave creature_body with no diffuseColor connection. We don't run
    # the stock export here (the harness drives both), but we DO assert
    # creature_body is the (or one of the) detected candidate(s).
    unrep = find_unrepresentable_materials(bpy.context.scene)
    unrep_names = [m.name for m in unrep]
    print(f"[test] unrepresentable materials: {unrep_names}", flush=True)
    assert "creature-body" in unrep_names, (
        f"expected creature-body in unrepresentable list, got {unrep_names}"
    )

    # Bake + export. Scope to creature-body so the test stays fast.
    baked = bake_and_export(
        usd_path,
        only_materials={"creature-body"},
        bake_resolution=512,
        # Preview surface is what we rewire.
        generate_preview_surface=True,
        # Skip MTLX path for this test (orthogonal to the bake).
        generate_materialx_network=False,
        # Only the body so the export is fast.
        selected_objects_only=False,
        export_animation=False,
    )

    print(f"[test] baked map: {baked}", flush=True)
    assert "creature-body" in baked, (
        f"creature_body was not baked; baked={baked}"
    )
    png_path = baked["creature-body"]
    assert os.path.isfile(png_path), f"baked PNG missing on disk: {png_path}"
    print(f"[test] baked PNG ok: {png_path}", flush=True)
    print(f"USD: {usd_path}", flush=True)

    # ---- Structural assertions via pxr.UsdShade --------------------------
    from pxr import Usd, UsdShade  # type: ignore

    stage = Usd.Stage.Open(usd_path)
    assert stage is not None, f"failed to open {usd_path}"

    target_mat = None
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material) and prim.GetName() in ("creature-body", "creature_body"):
            target_mat = UsdShade.Material(prim)
            break
    assert target_mat is not None, "creature_body material prim missing"

    # Find UsdPreviewSurface child.
    preview = None
    for child in target_mat.GetPrim().GetChildren():
        if child.IsA(UsdShade.Shader):
            sh = UsdShade.Shader(child)
            if sh.GetIdAttr().Get() == "UsdPreviewSurface":
                preview = sh
                break
    assert preview is not None, "UsdPreviewSurface missing on creature_body"

    diff_in = preview.GetInput("diffuseColor")
    assert diff_in is not None, "diffuseColor input missing"
    src = diff_in.GetConnectedSource()
    assert src is not None and src[0] is not None, (
        "diffuseColor is not connected after rewire"
    )
    api, src_name, _ = src
    src_shader = UsdShade.Shader(api.GetPrim())
    assert src_shader.GetIdAttr().Get() == "UsdUVTexture", (
        f"diffuseColor source should be UsdUVTexture, got {src_shader.GetIdAttr().Get()}"
    )

    # The file input must point at the baked PNG.
    file_in = src_shader.GetInput("file")
    assert file_in is not None, "UsdUVTexture.file missing"
    file_val = file_in.Get()
    file_path = str(file_val.path) if file_val else ""
    print(f"[test] diffuseColor texture file: {file_path}", flush=True)
    assert "bake" in file_path and file_path.endswith(".png"), (
        f"unexpected baked file path: {file_path}"
    )

    # Confirm ST is wired through UsdPrimvarReader_float2.
    st_in = src_shader.GetInput("st")
    assert st_in is not None, "UsdUVTexture.st input missing"
    st_src = st_in.GetConnectedSource()
    assert st_src is not None and st_src[0] is not None, "UsdUVTexture.st not connected"
    st_api, _, _ = st_src
    st_shader = UsdShade.Shader(st_api.GetPrim())
    assert st_shader.GetIdAttr().Get() == "UsdPrimvarReader_float2", (
        f"st source should be UsdPrimvarReader_float2, got {st_shader.GetIdAttr().Get()}"
    )
    varname = st_shader.GetInput("varname").Get()
    assert varname == "st", f"expected varname='st', got {varname}"

    print("[test] PASS: creature_body diffuseColor is wired to baked PNG via st.",
          flush=True)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except AssertionError as e:
        print(f"[test] FAIL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        rc = 1
    except Exception as e:
        print(f"[test] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        rc = 2
    sys.exit(rc)
