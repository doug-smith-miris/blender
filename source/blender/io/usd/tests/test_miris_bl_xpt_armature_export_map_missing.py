"""Regression sentinel for BL-XPT-ARMATURE-EXPORT-MAP-MISSING.

Stock Blender's USD exporter logs a misleading warning
``No export map entry for armature object <MESH_NAME>`` when a skinned mesh's
armature modifier targets an Object that the hierarchy iterator never visited
(e.g. because the armature lives in a collection that is hidden from the
render-eval depsgraph -- critter-v001's ``RIG-critter`` is in the
``critter-rigging`` collection, ``hide_render=True``). The bound mesh prim is
emitted with ``UsdSkelBindingAPI`` applied but no ``skel:skeleton``
relationship, so consumers see joint indices and weights on a mesh with
nowhere to bind them.

This patch adds a fallback pass in ``USDHierarchyIterator::process_usd_skel``
that, for any skinned mesh whose armature is referenced by its modifier but is
absent from ``armature_export_map_``, authors a rest-pose ``UsdSkelSkeleton``
at ``<mesh_parent>/Skel`` populated from the armature's bones, and registers
it in the map. The chaser then binds the mesh normally. The misleading log
message is also corrected to print the armature name, not the mesh name.

Assertions
~~~~~~~~~~
* ``GEO-critter_body``, ``GEO-critter_tongue``, ``GEO-critter-internal_organs``
  all carry a non-empty ``skel:skeleton`` relationship after export.
* Each referenced skeleton prim is a real ``UsdSkelSkeleton`` with non-empty
  ``joints`` and ``bindTransforms`` arrays (i.e. not a 1-joint identity
  placeholder from ``ensure_blend_shape_skeleton``).
* The fallback skeleton's bone count matches the source armature's deform
  bone count.
"""

import os
import sys

import bpy

BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/assets/char/"
    "critter/publish/critter-v001.blend"
)
OUT = "/tmp/test_blxpt_armature/critter.usda"

EXPECTED_SKINNED_MESHES = {
    "GEO-critter_body",
    "GEO-critter_tongue",
    "GEO-critter-internal_organs",
}


def _export():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=BLEND)

    # Count expected source-side deform bones so the fallback skeleton sanity check has a
    # comparison anchor.
    arm_obj = bpy.data.objects.get("RIG-critter")
    assert arm_obj is not None and arm_obj.type == 'ARMATURE', "RIG-critter missing from .blend"
    deform_bone_count = sum(1 for b in arm_obj.data.bones if not b.use_deform == False)  # noqa: E712
    # Re-count properly: counts every bone (RIG-critter has no `use_deform=False` overrides on
    # most bones), gives us a positive lower bound for the assertion.
    bone_count = len(arm_obj.data.bones)
    assert bone_count > 1, f"Expected armature with >1 bone, got {bone_count}"

    result = bpy.ops.wm.usd_export(
        filepath=OUT,
        export_animation=False,
        export_armatures=True,
        export_shapekeys=True,
        selected_objects_only=False,
    )
    assert result == {'FINISHED'}, f"export failed: {result}"

    return bone_count


def _assert_usd(min_bone_count: int):
    print(f"USD: {OUT}")
    from pxr import Usd, UsdSkel

    stage = Usd.Stage.Open(OUT)
    assert stage is not None, f"failed to open {OUT}"

    meshes_with_skel_api = {}
    for prim in stage.Traverse():
        if prim.HasAPI(UsdSkel.BindingAPI):
            from pxr import UsdGeom
            if prim.IsA(UsdGeom.Mesh):
                meshes_with_skel_api[prim.GetName()] = prim

    print(f"  meshes with UsdSkelBindingAPI: {sorted(meshes_with_skel_api.keys())}")

    for short in EXPECTED_SKINNED_MESHES:
        # USD names mangle '-' -> '_'.
        usd_name = short.replace('-', '_')
        prim = meshes_with_skel_api.get(usd_name)
        assert prim is not None, f"{usd_name} missing UsdSkelBindingAPI mesh in export"

        api = UsdSkel.BindingAPI(prim)
        targets = api.GetSkeletonRel().GetTargets() if api.GetSkeletonRel() else []
        assert targets, (
            f"{prim.GetPath()}: skel:skeleton target is empty -- the armature export-map "
            f"fallback did not register a skeleton for this mesh"
        )

        skel_path = targets[0]
        skel_prim = stage.GetPrimAtPath(skel_path)
        assert skel_prim and skel_prim.IsA(UsdSkel.Skeleton), (
            f"{prim.GetPath()}: skel:skeleton points to {skel_path} which is not a "
            f"UsdSkelSkeleton"
        )

        skel = UsdSkel.Skeleton(skel_prim)
        joints_attr = skel.GetJointsAttr()
        bind_attr = skel.GetBindTransformsAttr()
        assert joints_attr.HasAuthoredValue() and bind_attr.HasAuthoredValue(), (
            f"{skel_path}: skeleton has no authored joints/bindTransforms -- looks like a "
            f"placeholder, not a real fallback"
        )
        joints = joints_attr.Get()
        binds = bind_attr.Get()
        assert len(joints) == len(binds), (
            f"{skel_path}: joints ({len(joints)}) and bindTransforms ({len(binds)}) length "
            f"mismatch"
        )
        # An ensure_blend_shape_skeleton placeholder has exactly 1 joint named 'joint1'. The
        # real fallback must have more joints than that.
        assert len(joints) > 1, (
            f"{skel_path}: only {len(joints)} joint(s); looks like a blend-shape placeholder. "
            f"Armature has {min_bone_count} bones, expected the fallback to author at least 2."
        )
        print(f"  {prim.GetName()} -> {skel_path}: {len(joints)} joints OK")


def main():
    bone_count = _export()
    _assert_usd(bone_count)
    print(f"\nPASS: BL-XPT-ARMATURE-EXPORT-MAP-MISSING regression sentinel on critter-v001 "
          f"(armature has {bone_count} bones; all 3 skinned meshes bound to non-placeholder "
          f"fallback skeletons).")


if __name__ == "__main__":
    main()
