"""Regression test for BL-MAT-007-multi-material-mesh-geomsubsets-missing.

Runs `bpy.ops.wm.usd_export` against a real AYON Project Gold asset
(`mikassa-v001.blend`) whose `GEO-mika-jacket_upper` mesh declares four
material slots (`mika-jacket`, `red`, `green`, `yellow`). All faces on
this mesh currently use slot 0, so the stock exporter drops slots 1-3
entirely: the exported USD shows `materials: 1, geom_subsets: 0` for the
jacket and the secondary slot materials never appear in the stage.

The fork patch makes the mesh writer:
  * author one `UsdShade.Material` prim per declared, non-empty slot,
  * keep binding the first slot to the parent Mesh (Hydra-GL compat),
  * and author one `UsdGeomSubset` of type materialBind per slot — using
    an empty `indices` array for slots with no faces using them.

For the jacket this turns `materials: 1, geom_subsets: 0` into
`materials: 4, geom_subsets: 4` and round-trips the slot definitions.

Run via:
  /path/to/patched/Blender --background \\
    <mikassa-v001.blend> --python <this-file> -- \\
    --output-usd /tmp/mikassa_test.usda
"""

import argparse
import os
import sys

import bpy

JACKET_OBJECT = "GEO-mika-jacket_upper"
JACKET_USD_NAME = "GEO_mika_jacket_upper"
EXPECTED_SLOT_MATERIALS = ["mika-jacket", "red", "green", "yellow"]
# Canonical sanitized USD identifiers — Blender's name sanitizer keeps these
# unchanged since they're all already valid USD tokens.
EXPECTED_USD_MATERIALS = ["mika_jacket", "red", "green", "yellow"]


def parse_args() -> argparse.Namespace:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1 :]
    else:
        args = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-usd", required=True)
    return parser.parse_args(args)


def make_everything_visible() -> None:
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)
    for coll in bpy.data.collections:
        coll.hide_viewport = False
        coll.hide_render = False
    for vl in bpy.context.scene.view_layers:
        for layer_coll in vl.layer_collection.children:
            layer_coll.exclude = False
            layer_coll.hide_viewport = False
    bpy.context.view_layer.update()


def main() -> None:
    cli = parse_args()
    out_usd = os.path.abspath(cli.output_usd)
    os.makedirs(os.path.dirname(out_usd), exist_ok=True)

    # Sanity check: the source asset must declare four slots on the jacket.
    jacket = bpy.data.objects.get(JACKET_OBJECT)
    assert jacket is not None, f"Test asset is missing object {JACKET_OBJECT!r}"
    blender_slot_names = [s.material.name if s.material else None for s in jacket.material_slots]
    assert blender_slot_names == EXPECTED_SLOT_MATERIALS, (
        f"Test asset's {JACKET_OBJECT} slot list changed: got "
        f"{blender_slot_names!r}, expected {EXPECTED_SLOT_MATERIALS!r}"
    )

    make_everything_visible()

    print(f"[bl-mat-007] exporting USD -> {out_usd}")
    result = bpy.ops.wm.usd_export(
        filepath=out_usd,
        export_textures_mode="KEEP",
        generate_preview_surface=True,
        export_materials=True,
        export_meshes=True,
        export_curves=False,
        export_lights=False,
        export_cameras=False,
        convert_world_material=False,
        use_instancing=False,
        evaluation_mode="RENDER",
        selected_objects_only=False,
    )
    assert "FINISHED" in result, f"usd_export did not finish cleanly: {result}"
    print(f"[bl-mat-007] EXPORTED_USD_PATH={out_usd}")

    from pxr import Usd, UsdGeom, UsdShade

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"Failed to open exported stage at {out_usd}"

    # Collect all Material prim names in the stage so we can verify the
    # secondary slot materials are emitted.
    all_material_names = {
        prim.GetName()
        for prim in stage.Traverse()
        if prim.GetTypeName() == "Material"
    }
    print(f"[bl-mat-007] total material prims: {len(all_material_names)}")

    # Find the jacket prim. The jacket lives somewhere under /root with a path
    # like /root/RIG_mikassa/GEO_mika_jacket_upper/GEO_mika_jacket_upper.
    jacket_prim = None
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Mesh":
            continue
        if prim.GetName() == JACKET_USD_NAME:
            # Prefer the rig-parented one over any GN brushstroke variant.
            if "BS_lines" not in prim.GetPath().pathString:
                jacket_prim = prim
                break
    assert jacket_prim is not None, (
        f"Couldn't find the jacket mesh prim ({JACKET_USD_NAME!r}) in the stage."
    )
    print(f"[bl-mat-007] jacket prim: {jacket_prim.GetPath().pathString}")

    # Direct binding on the parent mesh should still be the first slot
    # (Hydra-GL compat).
    binding_api = UsdShade.MaterialBindingAPI(jacket_prim)
    rel = binding_api.GetDirectBindingRel()
    direct_targets = [t.pathString.rsplit("/", 1)[-1] for t in rel.GetTargets()] if rel else []
    assert direct_targets == ["mika_jacket"], (
        f"Regression: expected direct binding to ['mika_jacket'] on the jacket "
        f"mesh, got {direct_targets!r}"
    )

    # Enumerate GeomSubsets under the jacket. We expect one per declared slot.
    subset_to_material = {}
    for child in jacket_prim.GetChildren():
        if child.GetTypeName() != "GeomSubset":
            continue
        subset = UsdGeom.Subset(child)
        sb = UsdShade.MaterialBindingAPI(child).GetDirectBindingRel()
        bound = (
            [t.pathString.rsplit("/", 1)[-1] for t in sb.GetTargets()] if sb else []
        )
        family = subset.GetFamilyNameAttr().Get()
        elem_type = subset.GetElementTypeAttr().Get()
        indices = subset.GetIndicesAttr().Get() or []
        assert str(family) == "materialBind", (
            f"Subset {child.GetName()!r} family is {family!r}, expected 'materialBind'"
        )
        assert str(elem_type) == "face", (
            f"Subset {child.GetName()!r} elementType is {elem_type!r}, expected 'face'"
        )
        assert bound, (
            f"Subset {child.GetName()!r} has no material binding."
        )
        subset_to_material[child.GetName()] = (bound[0], len(indices))

    print(f"[bl-mat-007] jacket subsets: {subset_to_material}")

    # ASSERT 1: every declared slot material must be authored as a USD Material prim.
    for expected in EXPECTED_USD_MATERIALS:
        assert expected in all_material_names, (
            f"Regression (BL-MAT-007): expected Material prim {expected!r} not "
            f"authored — slot was silently dropped. All materials: "
            f"{sorted(all_material_names)}"
        )

    # ASSERT 2: a UsdGeomSubset of family materialBind exists for each declared slot.
    bound_slot_materials = {m for (m, _) in subset_to_material.values()}
    for expected in EXPECTED_USD_MATERIALS:
        assert expected in bound_slot_materials, (
            f"Regression (BL-MAT-007): no GeomSubset on the jacket binds to "
            f"{expected!r}. Bound subset materials: {bound_slot_materials!r}"
        )

    # ASSERT 3: at least the slot 0 subset must have a non-empty face index list,
    # since every face of the jacket uses material_index=0.
    slot0_count = subset_to_material["mika_jacket"][1]
    assert slot0_count > 0, (
        f"Regression: GeomSubset for slot 0 ('mika_jacket') has 0 face indices, "
        f"but all jacket faces use material_index=0 in the source mesh."
    )

    print("[bl-mat-007] PASS")


if __name__ == "__main__":
    main()
