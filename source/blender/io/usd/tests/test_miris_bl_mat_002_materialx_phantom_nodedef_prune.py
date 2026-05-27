"""BL-MAT-002 — MaterialX phantom-nodedef pruning (swarmfish-* lineage).

Stock Blender's MaterialX writer emits node categories the bundled USD MaterialX
library can't resolve (on swarmfish: a `Voronoi Texture`/`thin_film_bsdf`-style
node in the group-expanded creature_eyes / creature_pupil / creature_BS networks).
`UsdMtlxRead` then logs `Unable to find the nodedef for 'node_NNN'` and leaves an
`info:id`-less Shader stub plus an orphan shader-typed input on the consumer. Karma
rejects the whole network with `Error 1067: Reference to undefined variable: out_N`
so the creature renders invisible.

The patched exporter scans the temp stage after `UsdMtlxRead` and removes (a) every
Shader prim with no authored `info:id` and (b) every surviving shader's token-typed
input with no value and no connection. This test exports the real swarmfish-v001 hero
with generate_materialx_network=True and asserts no Shader prim is left without an
info:id, while the three MaterialX surface arcs survive.

Run:
  <patched-blender> --background <swarmfish-v001.blend> --python this_script.py
Regression sentinel: stock Blender 5.1.1 leaves 3 Shader prims with no info:id.
"""
import bpy
import os
import sys
import tempfile

from pxr import Usd, UsdShade

BLEND = bpy.data.filepath
OUT = os.path.join(tempfile.gettempdir(), "test_bl_mat_002_swarmfish_mtlx.usda")


def export():
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)
    bpy.ops.wm.usd_export(
        filepath=OUT,
        check_existing=False,
        selected_objects_only=False,
        evaluation_mode="RENDER",
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=True,
        convert_world_material=True,
        root_prim_path="/root",
    )
    print(f"USD_EXPORT_PATH={OUT}")


def validate():
    stage = Usd.Stage.Open(OUT)

    missing_id = []
    total_shaders = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader":
            continue
        total_shaders += 1
        shader = UsdShade.Shader(prim)
        id_attr = shader.GetIdAttr()
        tok = id_attr.Get() if id_attr else None
        if not tok:
            missing_id.append(prim.GetPath().pathString)

    # The three group-expanded creature materials must still carry an mtlx surface arc.
    mtlx_arcs = {}
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        mat = UsdShade.Material(prim)
        for out in mat.GetOutputs():
            if "mtlx" in out.GetBaseName() and out.GetAttr().GetConnections():
                mtlx_arcs[prim.GetName()] = out.GetAttr().GetConnections()[0].pathString

    print(f"TOTAL_SHADERS={total_shaders}")
    print(f"SHADERS_MISSING_INFO_ID={len(missing_id)}")
    for p in missing_id:
        print(f"  missing_id: {p}")
    print(f"MTLX_SURFACE_ARCS={len(mtlx_arcs)}")
    for name, tgt in sorted(mtlx_arcs.items()):
        print(f"  {name} -> {tgt}")

    assert len(missing_id) == 0, (
        f"{len(missing_id)} Shader prim(s) left without info:id (phantom nodedef stubs): "
        f"{missing_id}"
    )
    for expected in ("creature_eyes", "creature_pupil", "creature_BS"):
        assert expected in mtlx_arcs, (
            f"expected MaterialX surface arc on '{expected}' material, got {sorted(mtlx_arcs)}"
        )
    print("BL_MAT_002_PASS: no phantom nodedef stubs; 3 mtlx surface arcs intact")


if __name__ == "__main__":
    export()
    validate()
