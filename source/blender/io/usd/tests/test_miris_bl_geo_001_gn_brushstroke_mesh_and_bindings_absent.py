"""Regression test for BL-GEO-001-gn-brushstroke-mesh-and-bindings-absent.

When a CURVES object is driven by Geometry Nodes (e.g. the brushstroke_tools
add-on used across the AYON Project Singularity test corpus), the depsgraph-
evaluated Curves data block can lose its material slot count even when the
source `Object` still holds the slot. The stock exporter then drops the USD
`material:binding` relationship for those curves.

This test drives a real AYON asset (critter-v001.blend) through the USD
exporter and asserts that brushstroke materials are emitted into
`/root/_materials/` and that their carrier `BasisCurves` prims have a
`material:binding` relationship pointing at the right material.

Run with the patched Blender binary:

  /path/to/build_darwin/bin/Blender.app/Contents/MacOS/Blender \\
      --background --python source/blender/io/usd/tests/test_miris_bl_geo_001_gn_brushstroke_mesh_and_bindings_absent.py
"""

import os
import sys
import tempfile
import traceback

import bpy

ASSET = "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/assets/char/critter/publish/critter-v001.blend"
EXPECTED_BRUSHSTROKE_MATS = ["BS-body", "BS-eye_connection", "critter-lip_line_BS"]


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def normalize(name):
    return name.replace("-", "_").replace(".", "_")


def main():
    bpy.ops.wm.open_mainfile(filepath=ASSET)

    tmp = tempfile.mkdtemp(prefix="bl_geo_001_")
    out = os.path.join(tmp, "critter.usda")

    try:
        bpy.ops.wm.usd_export(
            filepath=out,
            check_existing=False,
            export_materials=True,
            export_curves=True,
            export_meshes=True,
            generate_preview_surface=True,
            evaluation_mode="RENDER",
            root_prim_path="/root",
        )
    except Exception as e:
        traceback.print_exc()
        fail(f"usd_export raised: {e}")

    print(f"USD_EXPORTED: {out}")

    try:
        from pxr import Usd, UsdGeom, UsdShade
    except ImportError as e:
        fail(f"pxr not importable from this Blender build: {e}")

    stage = Usd.Stage.Open(out)
    if not stage:
        fail("Failed to open exported stage")

    materials_present = {prim.GetPath().name for prim in stage.Traverse() if prim.IsA(UsdShade.Material)}
    print(f"materials_present ({len(materials_present)}): {sorted(materials_present)}")

    for blend_name in EXPECTED_BRUSHSTROKE_MATS:
        usd_name = normalize(blend_name)
        if usd_name not in materials_present:
            fail(
                f"Material {blend_name!r} (expected USD name {usd_name!r}) was not emitted into the stage. "
                f"Brushstroke material binding regression — see BL-GEO-001."
            )

    bindings_found = {}
    for prim in stage.Traverse():
        if not (prim.IsA(UsdGeom.BasisCurves) or prim.IsA(UsdGeom.NurbsCurves)):
            continue
        binding = UsdShade.MaterialBindingAPI(prim).GetDirectBinding()
        mat_path = binding.GetMaterialPath().pathString
        if mat_path:
            bindings_found.setdefault(mat_path, []).append(prim.GetPath().pathString)

    print(f"curve_bindings_found ({len(bindings_found)}):")
    for k, v in bindings_found.items():
        print(f"  {k}: {len(v)} curve prim(s)")

    for blend_name in EXPECTED_BRUSHSTROKE_MATS:
        usd_name = normalize(blend_name)
        target_path = f"/root/_materials/{usd_name}"
        if target_path not in bindings_found:
            fail(
                f"No BasisCurves prim binds to {target_path}. Expected at least one carrier curve "
                f"for material {blend_name!r}. BL-GEO-001 regression."
            )

    print("PASS: BL-GEO-001 brushstroke material bindings present.")


if __name__ == "__main__":
    main()
