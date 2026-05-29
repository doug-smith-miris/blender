"""
Test: critter-mtlx-faithful-bake — extend PR #31's bake-unrepresentable-albedo
to recognize critter's body_purple / body_mouth_bag patterns and rewire the
MaterialX surface arc so Karma (which only reads `outputs:mtlx:surface` per
PR #35's default) sees the baked diffuse.

Three critter materials exercise the extension:
  * body_purple    — Principled BSDF wrapped in a ShaderNodeGroup
                     (`critter-body_surface`), Base Color reached through
                     Hue/Saturation/Value (unrepresentable); stock PR #31
                     finds NO Principled at the outer scope and never bakes.
  * body_mouth_bag — Same inner group AND an outer Mix Shader fed by
                     Light Path (visibility cutout); stock PR #31 misses
                     both signatures.
  * critter-tongue — Simple constant Base Color; must NOT be flagged as
                     unrepresentable (no-regression sentinel).

Drive with the PATCHED build:
    .../build_darwin/bin/Blender.app/Contents/MacOS/Blender \
        --background <critter-v001.blend> \
        --python test_miris_critter_mtlx_faithful_bake.py
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
    _find_principled_recursive,
    _outer_surface_has_lightpath_cutout,
    _base_color_chain_is_unrepresentable,
)

CORPUS_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/assets/char/"
    "critter/publish/critter-v001.blend"
)


def _open_critter() -> None:
    if not bpy.data.filepath or "critter-v001" not in bpy.data.filepath:
        if not os.path.isfile(CORPUS_BLEND):
            raise FileNotFoundError(f"critter-v001.blend missing at {CORPUS_BLEND}")
        bpy.ops.wm.open_mainfile(filepath=CORPUS_BLEND)


def _resolve(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    for m in bpy.data.materials:
        if m.name.split(".")[0] == name:
            return m
    raise AssertionError(f"material '{name}' not found in critter scene")


def main() -> int:
    _open_critter()

    # ---- 1) Per-material detection asserts (data-extension proof) ---------
    body_purple = _resolve("body_purple")
    body_mouth_bag = _resolve("body_mouth_bag")
    critter_tongue = _resolve("critter-tongue")

    # Both have their Principled BSDF nested inside the `critter-body_surface`
    # group; recursive search must find it.
    for nm, mat in [
        ("body_purple", body_purple),
        ("body_mouth_bag", body_mouth_bag),
        ("critter-tongue", critter_tongue),
    ]:
        bsdf = _find_principled_recursive(mat.node_tree)
        assert bsdf is not None, f"recursive BSDF search failed on {nm}"

    # body_mouth_bag has a Light-Path-driven Mix Shader at the outer surface;
    # body_purple and critter-tongue do not.
    assert _outer_surface_has_lightpath_cutout(body_mouth_bag), (
        "body_mouth_bag should be detected as outer Light-Path cutout"
    )
    assert not _outer_surface_has_lightpath_cutout(body_purple), (
        "body_purple has no outer Light-Path cutout"
    )
    assert not _outer_surface_has_lightpath_cutout(critter_tongue), (
        "critter-tongue has no outer Light-Path cutout"
    )

    # The full unrepresentable-color test must accept body_purple +
    # body_mouth_bag and reject critter-tongue.
    assert _base_color_chain_is_unrepresentable(body_purple), (
        "body_purple's nested-group HSV chain should be marked unrepresentable"
    )
    assert _base_color_chain_is_unrepresentable(body_mouth_bag), (
        "body_mouth_bag's outer cutout should be marked unrepresentable"
    )
    assert not _base_color_chain_is_unrepresentable(critter_tongue), (
        "critter-tongue's constant Base Color must NOT be flagged unrepresentable"
    )

    unrep = {m.name for m in find_unrepresentable_materials(bpy.context.scene)}
    assert body_purple.name in unrep, f"body_purple missing from scene scan: {unrep}"
    assert body_mouth_bag.name in unrep, (
        f"body_mouth_bag missing from scene scan: {unrep}"
    )
    print(f"[test] detection ok: body_purple+body_mouth_bag flagged; "
          f"critter-tongue clean", flush=True)

    # ---- 2) Bake + dual-arc rewire ----------------------------------------
    out_dir = "/tmp/critter_mtlx_bake"
    os.makedirs(out_dir, exist_ok=True)
    usd_path = os.path.join(out_dir, "critter_baked.usda")

    baked = bake_and_export(
        usd_path,
        only_materials={body_purple.name, body_mouth_bag.name},
        bake_resolution=512,
        # PR #35's `emit_preview_surface_alongside_materialx` flag: with
        # both arcs authored we can verify *both* rewires landed; in the
        # production default-mtlx-only case the MaterialX rewire is the
        # one Karma actually reads.
        generate_preview_surface=True,
        generate_materialx_network=True,
        emit_preview_surface_alongside_materialx=True,
        selected_objects_only=False,
        export_animation=False,
    )

    print(f"[test] baked map: {baked}", flush=True)
    assert body_purple.name in baked, (
        f"body_purple was not baked; baked={baked}"
    )
    assert body_mouth_bag.name in baked, (
        f"body_mouth_bag was not baked; baked={baked}"
    )

    for mat_name in (body_purple.name, body_mouth_bag.name):
        png_path = baked[mat_name]
        assert os.path.isfile(png_path), f"baked PNG missing on disk for {mat_name}: {png_path}"
    print(f"USD: {usd_path}", flush=True)

    # ---- 3) Structural assertions via pxr.UsdShade ------------------------
    from pxr import Usd, UsdShade  # type: ignore

    stage = Usd.Stage.Open(usd_path)
    assert stage is not None, f"failed to open {usd_path}"

    def _find_mat(prim_name: str) -> UsdShade.Material:
        for prim in stage.Traverse():
            if prim.IsA(UsdShade.Material) and prim.GetName() == prim_name:
                return UsdShade.Material(prim)
        return None

    for blender_name in (body_purple.name, body_mouth_bag.name):
        usd_name = blender_name.replace("-", "_").replace(".", "_").replace(" ", "_")
        mat = _find_mat(usd_name) or _find_mat(blender_name)
        assert mat is not None, f"USD material prim for {blender_name} missing"

        # (a) UsdPreviewSurface.diffuseColor must point at the baked PNG.
        preview = None
        for child in mat.GetPrim().GetChildren():
            if child.IsA(UsdShade.Shader):
                sh = UsdShade.Shader(child)
                if sh.GetIdAttr().Get() == "UsdPreviewSurface":
                    preview = sh
                    break
        assert preview is not None, (
            f"UsdPreviewSurface missing on {blender_name}"
        )
        diff_in = preview.GetInput("diffuseColor")
        assert diff_in is not None and diff_in.HasConnectedSource(), (
            f"{blender_name}.diffuseColor not connected after rewire"
        )
        src = diff_in.GetConnectedSource()
        src_shader = UsdShade.Shader(src[0].GetPrim())
        assert src_shader.GetIdAttr().Get() == "UsdUVTexture", (
            f"{blender_name}.diffuseColor source should be UsdUVTexture, got "
            f"{src_shader.GetIdAttr().Get()}"
        )
        file_in = src_shader.GetInput("file")
        file_val = file_in.Get() if file_in else None
        file_path = str(file_val.path) if file_val else ""
        assert "bake" in file_path and file_path.endswith(".png"), (
            f"{blender_name} UsdUVTexture.file unexpected: {file_path}"
        )

        # (b) MaterialX `mtlx:surface` must be wired to an
        # ND_open_pbr_surface_surfaceshader whose base_color reads the
        # baked image via ND_image_color3 + ND_geompropvalue_vector2.
        mtlx_out = mat.GetSurfaceOutput("mtlx")
        assert mtlx_out is not None and mtlx_out.HasConnectedSource(), (
            f"{blender_name} mtlx:surface missing connection"
        )
        msrc = mtlx_out.GetConnectedSource()
        msurf = UsdShade.Shader(msrc[0].GetPrim())
        assert msurf.GetIdAttr().Get() == "ND_open_pbr_surface_surfaceshader", (
            f"{blender_name} mtlx:surface should be replaced with "
            f"ND_open_pbr_surface_surfaceshader, got {msurf.GetIdAttr().Get()}"
        )
        bc_in = msurf.GetInput("base_color")
        assert bc_in is not None and bc_in.HasConnectedSource(), (
            f"{blender_name} open_pbr_surface.base_color not connected"
        )
        bc_src = bc_in.GetConnectedSource()
        bc_shader = UsdShade.Shader(bc_src[0].GetPrim())
        assert bc_shader.GetIdAttr().Get() == "ND_image_color3", (
            f"{blender_name} base_color source should be ND_image_color3, "
            f"got {bc_shader.GetIdAttr().Get()}"
        )
        bc_file = bc_shader.GetInput("file")
        bc_file_val = bc_file.Get() if bc_file else None
        bc_file_path = str(bc_file_val.path) if bc_file_val else ""
        assert "bake" in bc_file_path and bc_file_path.endswith(".png"), (
            f"{blender_name} ND_image_color3.file unexpected: {bc_file_path}"
        )

        # MaterialX texcoord must be wired to ND_geompropvalue_vector2.
        tc_in = bc_shader.GetInput("texcoord")
        assert tc_in is not None and tc_in.HasConnectedSource(), (
            f"{blender_name} ND_image_color3.texcoord not connected"
        )
        tc_src = tc_in.GetConnectedSource()
        tc_shader = UsdShade.Shader(tc_src[0].GetPrim())
        assert tc_shader.GetIdAttr().Get() == "ND_geompropvalue_vector2", (
            f"{blender_name} texcoord source should be ND_geompropvalue_vector2"
        )

    print("[test] PASS: critter body_purple + body_mouth_bag rewired on BOTH "
          "UsdPreviewSurface.diffuseColor AND mtlx open_pbr_surface.base_color.",
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
