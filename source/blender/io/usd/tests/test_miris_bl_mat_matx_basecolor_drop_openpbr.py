"""Regression test for BL-MAT-MATX-BASECOLOR-DROP-OPENPBR.

Stock Blender exports critter-v001's body_purple / body_yellow / body_mouth_bag
materials with their MaterialX OpenPBR `base_color` set to the magenta
`(1, 0, 1)` sentinel that `DefaultMaterialNodeParser::compute_error()` emits
when the writer cannot find an output node — because the source `.blend` only
has SHD_OUTPUT_CYCLES (and/or SHD_OUTPUT_EEVEE) Material Outputs, never
SHD_OUTPUT_ALL, so `ntreeShaderOutputNode(local_tree, SHD_OUTPUT_ALL)`
returns null. The UsdPreviewSurface arc is unaffected (different code path),
which is the asymmetry that localizes the bug to the MaterialX writer.

After this fix (`source/blender/nodes/shader/materialx/material.cc`):
1. The inliner is invoked with `params.target_engine_` set to whichever
   engine the source material actually has output for (preferring ALL, then
   CYCLES, then EEVEE).
2. `ntreeShaderOutputNode` is called with that same target so the post-inline
   lookup succeeds.

Run from the patched Blender binary:
    /path/to/build_darwin/bin/Blender.app/Contents/MacOS/Blender --background \\
      --python source/blender/io/usd/tests/test_miris_bl_mat_matx_basecolor_drop_openpbr.py
"""
import bpy
import os
import sys
import tempfile

CRITTER_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/critter/publish/critter-v001.blend"
)

# Materials in critter-v001 that have only renderer-specific Material Output
# nodes (no SHD_OUTPUT_ALL). All three pre-fix emit magenta in their MaterialX
# open_pbr_surface.base_color.
TARGET_MATERIALS = ["body_purple", "body_yellow", "body_mouth_bag"]


def _collect_base_color_inputs(mat_prim):
    """Walk every UsdShadeShader descendant of a material prim and return
    a list of (path, value) for every input named 'base_color'."""
    from pxr import Usd, UsdShade

    found = []
    for desc in Usd.PrimRange(mat_prim):
        if not desc.IsA(UsdShade.Shader):
            continue
        sd = UsdShade.Shader(desc)
        for inp in sd.GetInputs():
            if inp.GetBaseName() != "base_color":
                continue
            attr = inp.GetAttr()
            has_connection = attr.HasAuthoredConnections()
            val = inp.Get()
            found.append((str(desc.GetPath()), val, has_connection))
    return found


def main():
    assert os.path.exists(CRITTER_BLEND), f"Missing real-corpus asset: {CRITTER_BLEND}"

    bpy.ops.wm.open_mainfile(filepath=CRITTER_BLEND)

    # Sanity: confirm body_purple has only renderer-specific Material Outputs
    # (the precondition for the bug to fire pre-fix).
    purple = bpy.data.materials.get("body_purple")
    assert purple is not None and purple.use_nodes
    targets = {
        n.target
        for n in purple.node_tree.nodes
        if n.type == "OUTPUT_MATERIAL"
    }
    assert "ALL" not in targets, (
        "Test precondition broken: body_purple now has a SHD_OUTPUT_ALL Material "
        f"Output (targets={targets!r}); this finding's regression sentinel "
        "depends on the asset having only renderer-specific outputs."
    )
    assert "CYCLES" in targets or "EEVEE" in targets, (
        f"Test precondition broken: body_purple has no renderer-specific Material "
        f"Output (targets={targets!r})."
    )

    out_dir = tempfile.mkdtemp(prefix="miris_basecolor_drop_")
    out_usd = os.path.join(out_dir, "critter.usda")
    bpy.ops.wm.usd_export(
        filepath=out_usd,
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=True,
        relative_paths=True,
    )
    assert os.path.exists(out_usd), f"USD export missing: {out_usd}"

    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"Could not open exported stage: {out_usd}"

    failures = []
    for mat_name in TARGET_MATERIALS:
        mat_prim = stage.GetPrimAtPath(f"/root/_materials/{mat_name}")
        assert mat_prim and mat_prim.IsValid(), f"Missing material prim: {mat_name}"
        mat = UsdShade.Material(mat_prim)

        # The MaterialX surface output ('mtlx' render context) must exist.
        src, _, _ = mat.ComputeSurfaceSource("mtlx")
        if not src:
            failures.append(
                f"{mat_name}: MaterialX surface source missing — exporter still "
                "took the compute_error() / empty fallback path."
            )
            continue

        # NO base_color input anywhere in the network may be the magenta sentinel.
        for path, val, has_conn in _collect_base_color_inputs(mat_prim):
            if val is None:
                continue
            try:
                r, g, b = val
            except Exception:
                continue
            if (
                abs(r - 1.0) < 1e-6
                and abs(g - 0.0) < 1e-6
                and abs(b - 1.0) < 1e-6
                and not has_conn
            ):
                failures.append(
                    f"{mat_name}: magenta sentinel base_color=(1,0,1) at {path} — "
                    "MaterialX writer dropped into compute_error() because the "
                    "inliner could not locate a Material Output for the chosen "
                    "engine target."
                )

    # body_purple specifically: the patched MaterialX network must include
    # critter-body-abledo.tif somewhere (the source Base Color texture).
    purple_prim = stage.GetPrimAtPath("/root/_materials/body_purple")
    files_referenced = set()
    for desc in Usd.PrimRange(purple_prim):
        if not desc.IsA(UsdShade.Shader):
            continue
        sd = UsdShade.Shader(desc)
        for inp in sd.GetInputs():
            if inp.GetBaseName() != "file":
                continue
            asset = inp.Get()
            if asset:
                files_referenced.add(asset.path)
    if not any("critter-body-abledo" in f for f in files_referenced):
        failures.append(
            "body_purple: MaterialX network does not reference "
            "critter-body-abledo.tif anywhere. Files seen: "
            f"{sorted(files_referenced)!r}"
        )

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print("PASS BL-MAT-MATX-BASECOLOR-DROP-OPENPBR")
    print(f"  USD: {out_usd}")
    print(
        "  Verified MaterialX OpenPBR base_color is connected (not magenta) on "
        f"{', '.join(TARGET_MATERIALS)} and body_purple references "
        "critter-body-abledo.tif."
    )


if __name__ == "__main__":
    main()
