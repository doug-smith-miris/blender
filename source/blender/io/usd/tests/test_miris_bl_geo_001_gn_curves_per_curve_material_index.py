"""Regression test for BL-GEO-001 (UsdGeomBasisCurves per-curve material binding).

Geometry-Nodes "Set Material" assigns materials per-curve via a curve-domain
`material_index` attribute on the realized Curves. Stock `USDCurvesWriter::assign_materials`
bound only the first non-empty slot to the whole curve prim and `break`-ed, so a curves
object carrying more than one material collapsed to a single binding and the per-curve look
was lost.

The fork's fix keeps that whole-prim binding (for renderers/viewports that ignore curve
subsets) and, when the realized curves carry a `material_index` attribute referencing two or
more materials, additionally authors one `UsdGeomSubset` (family=materialBind,
elementType=curve) per material -- mirroring `USDGenericMeshWriter::assign_materials`.

This drives the real Project-Singularity `swarmfish-v001.blend`:

  * PART A asserts the already-landed binding-recovery invariant on the asset's real GN
    brushstroke carrier: `creature_BS` is emitted and bound to a BasisCurves prim (no
    regression of the eval+original-object slot fallback).

  * PART B exercises the new per-curve path with a deterministic 4-curve, 2-material Curves
    object added to the scene (material_index = [0,0,1,1]) and asserts the exported
    BasisCurves carries exactly two materialBind GeomSubsets (elementType=curve) bound to the
    correct materials over curve indices [0,1] and [2,3].

Regression sentinel: stock Blender 5.x emits ZERO GeomSubsets for PART B (single bind+break);
the patched build emits two. Run (MUST use the patched build):
  <patched>/Blender --background <swarmfish-v001.blend> --python this_file.py
"""
import sys

import bpy

OUT = "/tmp/test_bl_geo_001_per_curve_material_index.usda"


def build_multimat_curves():
    """Add a plain (non-GN) poly Curves object with two materials and a per-curve
    material_index to the current scene. No GN modifier => the material_index survives
    evaluation unchanged, exercising the writer's subset path deterministically."""
    curves = bpy.data.hair_curves.new("BLGEO001_multimat")
    curves.add_curves([2, 2, 2, 2])  # 4 curves, 8 points

    positions = [
        (0.0, 0.0, 0.0), (0.0, 0.0, 1.0),
        (1.0, 0.0, 0.0), (1.0, 0.0, 1.0),
        (2.0, 0.0, 0.0), (2.0, 0.0, 1.0),
        (3.0, 0.0, 0.0), (3.0, 0.0, 1.0),
    ]
    pos_attr = curves.attributes["position"]
    for i, p in enumerate(pos_attr.data):
        p.vector = positions[i]

    ct = curves.attributes.new("curve_type", "INT8", "CURVE")
    for d in ct.data:
        d.value = 0  # CURVE_TYPE_CATMULL_ROM is default; 0 == poly is fine for binding

    mi = curves.attributes.new("material_index", "INT", "CURVE")
    for i, d in enumerate(mi.data):
        d.value = 0 if i < 2 else 1

    mat_a = bpy.data.materials.new("BLGEO001_matA")
    mat_b = bpy.data.materials.new("BLGEO001_matB")
    curves.materials.append(mat_a)
    curves.materials.append(mat_b)

    obj = bpy.data.objects.new("BLGEO001_multimat", curves)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def main():
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False

    build_multimat_curves()

    bpy.ops.wm.usd_export(
        filepath=OUT,
        check_existing=False,
        selected_objects_only=False,
        export_animation=False,
        export_hair=True,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        root_prim_path="/root",
    )
    print("USD_EXPORT_PATH:", OUT)

    from pxr import Usd, UsdShade, UsdGeom

    stage = Usd.Stage.Open(OUT)

    # ---- PART A: binding-recovery invariant on the real GN brushstroke carrier ----
    mats = {p.GetName() for p in stage.Traverse() if p.GetTypeName() == "Material"}
    print("Materials:", sorted(mats))
    assert "creature_BS" in mats, (
        "creature-BS was dropped from the USD material set: %s" % sorted(mats)
    )
    bound_bs = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "BasisCurves":
            continue
        path = UsdShade.MaterialBindingAPI(prim).GetDirectBinding().GetMaterialPath()
        if path and path.name == "creature_BS":
            bound_bs.append(str(prim.GetPath()))
    print("creature_BS bound on:", bound_bs)
    assert bound_bs, "creature-BS material is not bound to any BasisCurves carrier"

    # ---- PART B: per-curve material_index -> GeomSubset on the multi-material curves ----
    target = None
    for prim in stage.Traverse():
        if prim.GetTypeName() != "BasisCurves":
            continue
        if "BLGEO001_multimat" in prim.GetName():
            target = prim
            break
    assert target is not None, "multi-material curves prim was not exported"
    print("multi-material curves prim:", target.GetPath())

    subsets = [c for c in target.GetChildren() if c.GetTypeName() == "GeomSubset"]
    print("GeomSubsets:", [s.GetName() for s in subsets])
    assert len(subsets) == 2, (
        "expected 2 materialBind GeomSubsets on the multi-material curves, got %d "
        "(stock Blender collapses to a single whole-prim bind)" % len(subsets)
    )

    expected = {  # subset bound-material name -> sorted curve indices
        "BLGEO001_matA": [0, 1],
        "BLGEO001_matB": [2, 3],
    }
    seen = {}
    for sub in subsets:
        gs = UsdGeom.Subset(sub)
        assert gs.GetFamilyNameAttr().Get() == "materialBind", "subset family != materialBind"
        assert gs.GetElementTypeAttr().Get() == "curve", (
            "subset elementType != curve: %s" % gs.GetElementTypeAttr().Get()
        )
        bound = UsdShade.MaterialBindingAPI(sub).ComputeBoundMaterial()[0]
        assert bound, "GeomSubset has no resolvable bound material"
        seen[bound.GetPrim().GetName()] = sorted(int(i) for i in gs.GetIndicesAttr().Get())

    print("subset bindings:", seen)
    assert seen == expected, "per-curve subset bindings mismatch: %s != %s" % (seen, expected)

    print("PASS: BL-GEO-001 per-curve material_index GeomSubsets authored + bound")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
