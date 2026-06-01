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

It additionally locks the *renderer-level* invariant the structural symptom maps to:
Karma's `Error 1067: Reference to undefined variable` is, structurally, a UsdShade
`.connect` whose target attribute does not resolve to an existing prim. So we walk
every connection on every connectable prim and assert none dangles — the structural
equivalent of "Karma compiles the network clean". This catches a future regression
that leaves a dangling reference even if it happens to author a non-empty info:id,
which the info:id-only check would miss.

Run:
  <patched-blender> --background <swarmfish-v001.blend> --python this_script.py
Regression sentinel (stock Blender): 2-3 Shader prims with no info:id AND the network
is connection-inconsistent; the matching Karma husk render logs `Error 1067` and the
isolated eye renders flat grey instead of shaded yellow with a dark pupil.
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

    # Connection consistency: every UsdShade connection must resolve to a prim that
    # exists on the stage. A dangling connection is exactly what makes Karma abort
    # with Error 1067 ("Reference to undefined variable"), so this is the structural
    # proxy for "the network compiles".
    dangling = []
    total_conns = 0
    for prim in stage.Traverse():
        connectable = UsdShade.ConnectableAPI(prim)
        if not connectable:
            continue
        for shade_attr in list(connectable.GetInputs()) + list(connectable.GetOutputs()):
            for tgt in shade_attr.GetAttr().GetConnections():
                total_conns += 1
                target_prim = stage.GetPrimAtPath(tgt.GetPrimPath())
                if not target_prim or not target_prim.IsValid():
                    dangling.append(f"{shade_attr.GetAttr().GetPath()} -> {tgt}")
    print(f"TOTAL_CONNECTIONS={total_conns}")
    print(f"DANGLING_CONNECTIONS={len(dangling)}")
    for d in dangling[:20]:
        print(f"  dangling: {d}")

    assert len(missing_id) == 0, (
        f"{len(missing_id)} Shader prim(s) left without info:id (phantom nodedef stubs): "
        f"{missing_id}"
    )
    assert len(dangling) == 0, (
        f"{len(dangling)} dangling UsdShade connection(s) (Karma Error 1067 trigger): "
        f"{dangling[:20]}"
    )
    for expected in ("creature_eyes", "creature_pupil", "creature_BS"):
        assert expected in mtlx_arcs, (
            f"expected MaterialX surface arc on '{expected}' material, got {sorted(mtlx_arcs)}"
        )
    print("BL_MAT_002_PASS: no phantom nodedef stubs; network connection-consistent "
          "(no Error 1067); 3 mtlx surface arcs intact")


if __name__ == "__main__":
    export()
    validate()
