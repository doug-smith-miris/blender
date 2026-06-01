"""Regression test for BL-GEO-001-gn-brushstroke-materials-absent.

Runs `bpy.ops.wm.usd_export` against a real AYON Singularity asset
(`swarmfish-v001.blend`) whose `GEO-swamfish - Brushstrokes` curves object
has a Geometry Nodes modifier that realizes brushstroke meshes carrying
the `creature-BS` material slot.

Before the fork patch, the USD exporter dispatched USDCurvesWriter on the
realized dupli (using the source object's curves data) and the brushstroke
mesh + its material binding never reached the stage. The fix builds a
shallow-copy Object with `DupliObject::ob_data` substituted into
`->data`/`->type`/`->runtime->data_eval`, and broadens
`USDGenericMeshWriter::assign_materials` to also walk the realized mesh's
`mat[]` slots when the source object's `totcol` is smaller.

Run via:
  /path/to/patched/Blender --background --enable-autoexec \\
    <swarmfish-v001.blend> --python <this-file> -- \\
    --output-usd /tmp/swarmfish_test.usda
"""

import argparse
import os
import sys

import bpy

ASSET_MATERIAL = "creature-BS"
# Canonical sanitized USD identifier corresponding to `creature-BS`.
USD_MATERIAL_NAME = "creature_BS"


def parse_args() -> argparse.Namespace:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1 :]
    else:
        args = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-usd", required=True)
    return parser.parse_args(args)


def make_everything_visible() -> None:
    """Unhide all objects + collections so the GN-driven brushstroke dupli
    reaches the export depsgraph. Matches the Cycles-reference setup from the
    diagnostic agent."""
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

    make_everything_visible()

    print(f"[bl-geo-001] exporting USD -> {out_usd}")
    result = bpy.ops.wm.usd_export(
        filepath=out_usd,
        export_textures_mode="KEEP",
        generate_preview_surface=True,
        export_materials=True,
        export_meshes=True,
        export_curves=True,
        export_lights=False,
        export_cameras=False,
        convert_world_material=False,
        use_instancing=False,
        evaluation_mode="RENDER",
        selected_objects_only=False,
    )
    assert "FINISHED" in result, f"usd_export did not finish cleanly: {result}"
    print(f"[bl-geo-001] EXPORTED_USD_PATH={out_usd}")

    # Assertions via pxr bindings.
    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"Failed to open exported stage at {out_usd}"

    material_names = []
    mesh_paths_with_creature_bs = []
    for prim in stage.Traverse():
        type_name = prim.GetTypeName()
        if type_name == "Material":
            material_names.append(prim.GetPath().pathString.rsplit("/", 1)[-1])
        elif type_name == "Mesh":
            binding_api = UsdShade.MaterialBindingAPI(prim)
            rel = binding_api.GetDirectBindingRel()
            if not rel:
                continue
            for target in rel.GetTargets():
                if target.pathString.rsplit("/", 1)[-1] == USD_MATERIAL_NAME:
                    mesh_paths_with_creature_bs.append(prim.GetPath().pathString)

    print(f"[bl-geo-001] material prims: {material_names}")
    print(f"[bl-geo-001] meshes bound to {USD_MATERIAL_NAME}: {mesh_paths_with_creature_bs}")

    # ASSERT 1: The brushstroke material must be present as a Material prim.
    assert USD_MATERIAL_NAME in material_names, (
        f"Regression: expected Material prim {USD_MATERIAL_NAME!r} "
        f"(from Blender material {ASSET_MATERIAL!r}) not found. "
        f"Got: {sorted(material_names)}"
    )

    # ASSERT 2: A Mesh prim must actually be bound to it (not just defined orphaned).
    assert mesh_paths_with_creature_bs, (
        f"Regression: {USD_MATERIAL_NAME!r} material exists but no Mesh prim is "
        f"bound to it. The GN-realized brushstroke mesh failed to reach the USD."
    )

    # ASSERT 3: At least one of the bound meshes should be the dupli realized
    # under the source curves object (path contains 'Brushstrokes'). This guards
    # against a future change that emits the material but on the wrong prim.
    assert any("Brushstrokes" in p for p in mesh_paths_with_creature_bs), (
        f"Regression: no Brushstrokes-named Mesh prim is bound to "
        f"{USD_MATERIAL_NAME!r}. Bound prims: {mesh_paths_with_creature_bs}"
    )

    print("[bl-geo-001] PASS")


if __name__ == "__main__":
    main()
