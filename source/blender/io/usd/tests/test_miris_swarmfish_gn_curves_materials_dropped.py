"""Regression test for `swarmfish-gn-curves-materials-dropped`.

Geometry-Nodes-driven Curves carriers (the brushstroke-tools fins/tail on Project
Singularity's swarmfish) lose their material binding on USD export: the GN-evaluated
Curves data block drops the artist-assigned material slot (or leaves its material
pointer null), so `USDCurvesWriter::assign_materials` -- which consulted only
`Object::totcol` + `BKE_object_material_get` -- bound nothing. Stock Blender exports
3 materials (creature_body/eyes/pupil) and zero bindings on any BasisCurves prim.

The fork's fix resolves the material via the eval-aware API and, when the evaluated
slot is empty, falls back to the *original* (pre-evaluation) Object that still holds
the authored slot. After the fix, `creature-BS` is emitted under /root/_materials and
bound to the brushstroke curve carriers.

Run (MUST use the patched build):
  <patched>/Blender --background <swarmfish-v001.blend> --python this_file.py
"""
import os
import sys

import bpy

SWARMFISH = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/swarmfish/publish/swarmfish-v001.blend"
)
OUT = "/tmp/test_swarmfish_gn_curves_materials.usda"


def main():
    # The .blend is loaded via --background <blend>; ensure nothing is force-hidden.
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False

    bpy.ops.wm.usd_export(
        filepath=OUT,
        check_existing=False,
        selected_objects_only=False,
        export_animation=False,
        export_curves=True,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        root_prim_path="/root",
    )
    print("USD_EXPORT_PATH:", OUT)

    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(OUT)

    mats = {
        p.GetName()
        for p in stage.Traverse()
        if p.GetTypeName() == "Material"
    }
    print("Materials:", sorted(mats))

    # The GN brushstroke material must now be emitted (was dropped pre-fix).
    assert "creature_BS" in mats, (
        "creature-BS was dropped from the USD material set: %s" % sorted(mats)
    )

    # ...and bound to at least one brushstroke BasisCurves carrier.
    bound_bs = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "BasisCurves":
            continue
        path = UsdShade.MaterialBindingAPI(prim).GetDirectBinding().GetMaterialPath()
        if path and path.name == "creature_BS":
            bound_bs.append(str(prim.GetPath()))
    print("creature_BS bound on:", bound_bs)
    assert bound_bs, "creature-BS material is not bound to any BasisCurves carrier"

    print("PASS: swarmfish-gn-curves-materials-dropped regression fixed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
