"""Regression test for `swarmfish-mtlx-dangling-ref-karma-compile-fail`.

Blender's MaterialX export of a Principled BSDF builds a stack of `layer` BSDF nodes.
When an upstream sub-node has no equivalent in the bundled MaterialX library (notably
`thin_film_bsdf`, which is absent), the generating NodeItem comes back empty and the
consuming `ND_layer_bsdf` is emitted with its `top` (or `base`) BSDF input left
unconnected. A `layer` with an empty `top`/`base` is invalid for downstream MaterialX
code generators: Karma aborts the whole shader compile with
"Error 1067: Reference to undefined variable", so every material renders default grey.

The fix in usd_writer_material.cc bypasses such degenerate `layer` nodes, redirecting
their consumers to the surviving connected input. The degenerate node is left orphaned
(no connection references its output), so a downstream code generator dead-code-eliminates
it instead of choking on it. This test exports the real swarmfish asset and asserts that
no degenerate `ND_layer_*` shader (one of top/base unconnected) is still *referenced* by
any connection in the MaterialX network — which is the condition that breaks Karma.

Run headless with the PATCHED build:
  build_darwin/.../Blender --background <swarmfish.blend> \
      --python test_miris_swarmfish_mtlx_dangling_ref.py
"""

import os
import sys
import tempfile

import bpy
from pxr import Usd, UsdShade

SWARMFISH_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/swarmfish/publish/swarmfish-v001.blend"
)


def export_swarmfish_mtlx():
    out_dir = tempfile.mkdtemp(prefix="miris_swarmfish_mtlx_")
    usd_path = os.path.join(out_dir, "swarmfish-mtlx.usda")

    # Unhide everything so geometry-nodes driven meshes (and their materials) survive.
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)

    bpy.ops.wm.usd_export(
        filepath=usd_path,
        check_existing=False,
        selected_objects_only=False,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        generate_materialx_network=True,
        root_prim_path="/root",
    )
    return usd_path


def degenerate_layer_outputs(stage):
    """Return the set of output attribute paths of degenerate ND_layer_* shaders
    (exactly one of top/base connected)."""
    outs = set()
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        if not shader:
            continue
        shader_id = shader.GetIdAttr().Get()
        if not shader_id or not str(shader_id).startswith("ND_layer_"):
            continue

        def is_connected(name):
            inp = shader.GetInput(name)
            return bool(inp) and len(inp.GetAttr().GetConnections()) == 1

        if is_connected("top") != is_connected("base"):  # exactly one connected => degenerate
            for o in shader.GetOutputs():
                outs.add(str(o.GetAttr().GetPath()))
    return outs


def connections_into(stage, target_attr_paths):
    """Return list of (consumer_attr, target) for connections pointing at target_attr_paths."""
    hits = []
    for prim in stage.Traverse():
        for attr in prim.GetAttributes():
            for c in attr.GetConnections():
                if str(c) in target_attr_paths:
                    hits.append((str(attr.GetPath()), str(c)))
    return hits


def main():
    usd_path = export_swarmfish_mtlx()
    print("MIRIS_EXPORTED_USD:", usd_path)

    stage = Usd.Stage.Open(usd_path)
    assert stage, "Failed to open exported USD stage"

    # Sanity: the MaterialX network was actually written (layer nodes exist).
    n_layers = sum(
        1
        for p in stage.Traverse()
        if UsdShade.Shader(p)
        and str(UsdShade.Shader(p).GetIdAttr().Get() or "").startswith("ND_layer_")
    )
    assert n_layers > 0, "No ND_layer_* nodes found — MaterialX network missing?"
    print(f"MIRIS_LAYER_NODE_COUNT: {n_layers}")

    deg_outputs = degenerate_layer_outputs(stage)
    print(f"MIRIS_DEGENERATE_LAYER_COUNT: {len(deg_outputs)}")

    # Degenerate layer nodes may remain in the graph, but they MUST be orphaned: no connection
    # may reference their output. A referenced degenerate layer is exactly what makes Karma's
    # MaterialX compile abort with "undefined variable".
    referencing = connections_into(stage, deg_outputs)
    assert not referencing, (
        "Degenerate ND_layer_* node output is still referenced — Karma will fail to compile:\n  "
        + "\n  ".join(f"{a} -> {t}" for a, t in referencing)
    )

    print("MIRIS_TEST_PASS: degenerate layer nodes are orphaned; MaterialX network is Karma-compilable")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
