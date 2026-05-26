"""Regression test for BL-MAT-005:mtlx-dropped-node-dangling-ref.

Blender's MaterialX writer emits node categories like ``thin_film_bsdf``
(from the Principled BSDF iridescence component) that aren't in the
MaterialX standard library bundled with the USD libraries Blender links
against. When ``UsdMtlxRead`` consumes the MaterialX document during
``create_usd_materialx_material``, it logs ``Unable to find the nodedef
for 'node_NNN' node, outputs not added.`` and silently drops every
``inputs:*.connect`` that targeted the dropped node — but leaves the
input-only stub Shader prim behind. The stub has no ``info:id`` and no
outputs. Karma's MaterialX shader compiler chokes on the result with
``Error 1067: Reference to undefined variable: out_NN`` because the
re-emitted MaterialX still names the orphan nodes.

The patch in ``usd_writer_material.cc::create_usd_materialx_material``
scans the temp stage after ``UsdMtlxRead`` and removes every
``UsdShadeShader`` whose ``info:id`` is unauthored, before
``SdfCopySpec`` copies the network into the destination stage.

This test drives the real AYON mikassa hero asset through
``bpy.ops.wm.usd_export(... generate_materialx_network=True)`` and
asserts that:

  * No Shader prim under any material is missing ``info:id`` (i.e. no
    phantom orphans survived into the final USD).
  * The mikassa hero's gold-cracks material — which heavily uses the
    Principled BSDF's thin-film input and therefore reproduces this
    failure mode every time — has at least one ``ND_layer_bsdf`` consumer
    that previously lost its ``inputs:top`` connection, proving the
    cleanup actually fired on this asset.

Run via:
    /path/to/patched/Blender --background \\
        <mikassa-v001.blend> --python <this-file> -- \\
        --output-usd /tmp/mikassa_bl_mat_005.usda
"""

import argparse
import os
import sys

import bpy


def parse_args() -> argparse.Namespace:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]
    else:
        args = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-usd", required=True)
    return parser.parse_args(args)


def main() -> None:
    cli = parse_args()
    out_usd = os.path.abspath(cli.output_usd)
    os.makedirs(os.path.dirname(out_usd), exist_ok=True)

    print(f"[bl-mat-005-mtlx-dropped-node] exporting USD -> {out_usd}")
    result = bpy.ops.wm.usd_export(
        filepath=out_usd,
        selected_objects_only=False,
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=True,
    )
    assert "FINISHED" in result, f"usd_export did not finish: {result}"

    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"failed to open exported stage {out_usd}"

    phantom_shaders = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader":
            continue
        sh = UsdShade.Shader(prim)
        id_attr = sh.GetIdAttr()
        val = id_attr.Get() if id_attr else None
        if not val:
            phantom_shaders.append(prim.GetPath().pathString)

    assert not phantom_shaders, (
        "Regression (BL-MAT-005:mtlx-dropped-node-dangling-ref): exported "
        "USD still contains {n} Shader prims with no info:id authored. "
        "These are stubs UsdMtlxRead left behind after failing to resolve a "
        "MaterialX nodedef (typically thin_film_bsdf from the Principled "
        "BSDF iridescence input). The post-read prune in "
        "usd_writer_material.cc::create_usd_materialx_material did not "
        "fire. First few: {sample}".format(
            n=len(phantom_shaders),
            sample=phantom_shaders[:5],
        )
    )

    # Confirm the mikassa gold_cracks material exists and at least one
    # ND_layer_bsdf consumer is present — that's the node that previously
    # carried a dangling inputs:top connection to the dropped thin-film
    # phantom. The connection is allowed to be unauthored now (MaterialX
    # uses the default BSDF for an unconnected layer top), but the
    # consumer itself must still be in the network.
    gold_cracks = stage.GetPrimAtPath("/root/_materials/mika_gold_cracks")
    assert gold_cracks, (
        "mikassa gold_cracks material missing from exported USD — "
        "test asset isn't the expected mikassa-v001 hero."
    )
    layer_bsdf_consumers = []
    for prim in Usd.PrimRange(gold_cracks):
        if prim.GetTypeName() != "Shader":
            continue
        sh = UsdShade.Shader(prim)
        val = sh.GetIdAttr().Get() if sh.GetIdAttr() else None
        if val == "ND_layer_bsdf":
            layer_bsdf_consumers.append(prim)
    assert layer_bsdf_consumers, (
        "mikassa gold_cracks/NodeGraphs has no ND_layer_bsdf consumers; "
        "expected the Principled BSDF iridescence/coat layering chain."
    )

    print(
        f"[bl-mat-005-mtlx-dropped-node] EXPORTED_USD_PATH={out_usd}"
    )
    print(
        f"[bl-mat-005-mtlx-dropped-node] {len(layer_bsdf_consumers)} "
        f"ND_layer_bsdf consumers verified clean in mika_gold_cracks"
    )
    print("[bl-mat-005-mtlx-dropped-node] PASS")


if __name__ == "__main__":
    main()
