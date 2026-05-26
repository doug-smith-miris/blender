# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration test for BL-MAT-002-materialx-dangling-refs-break-karma-compile.

Blender's `Principled BSDF` MaterialX writer
(`source/blender/nodes/shader/nodes/node_shader_bsdf_principled.cc`) builds a
`thin_film_bsdf` MaterialX node unconditionally when emitting the standard
BSDF chain.  MaterialX 1.39's standard library has no `thin_film_bsdf` NodeDef
(thin-film is only exposed as an internal layer of `standard_surface` /
`open_pbr_surface`).  Without intervention, `pxr::UsdMtlxRead` then warns
"Unable to find the nodedef for 'node_NN' node, outputs not added.", emits a
`def Shader` prim with no `info:id`, and silently drops the downstream
`inputs:top.connect` on the `ND_layer_bsdf` layering thin-film on the metal
mix.  Karma then fails to compile the resulting graph with
"Reference to undefined variable: out_N" because that BSDF input is left
declared but unconnected.

Fix: `substitute_undefined_materialx_nodes` in `usd_writer_material.cc` walks
the MaterialX document before it reaches `UsdMtlxRead` and rewrites any node
whose category has no NodeDef in the bundled standard library into a typed
identity (`oren_nayar_diffuse_bsdf` with `weight=0` for BSDF outputs,
`uniform_edf` with `color=(0,0,0)` for EDF outputs, `constant` with a zero
value for value-typed outputs).  Rewriting preserves the node name so
downstream connections survive.

This test drives swarmfish-v001.blend through `wm.usd_export` with
`generate_materialx_network=True` and prints the exported path on a
marker line; `validate_swarmfish_materialx_dangling_refs.py` then opens the
USD via `pxr.UsdShade` and asserts (a) every Shader prim under the
MaterialX-bearing materials has an `info:id`, (b) the `thin_film_bsdf`
substitution actually ran (a node named after the original lands as
`ND_oren_nayar_diffuse_bsdf` with weight=0), and (c) the `layer_bsdf`
that consumed thin-film still has an `inputs:top.connect` (the regression
sentinel for the dangling-ref symptom).

Invocation:

    /Users/d.smith/MirisProjects/build_darwin/bin/Blender.app/Contents/MacOS/Blender \\
        --background \\
        --python source/blender/io/usd/docs/tests/test_swarmfish_materialx_dangling_refs.py

Prints ``EXPORTED_USDA=<path>`` for the validation step
(``validate_swarmfish_materialx_dangling_refs.py``).
"""

import os
import sys
import tempfile

import bpy


SWARMFISH_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/swarmfish/publish/swarmfish-v001.blend"
)


def main() -> int:
    blend_path = SWARMFISH_BLEND
    if bpy.data.filepath:
        blend_path = bpy.data.filepath
    elif not os.path.isfile(blend_path):
        print(
            f"SKIP: swarmfish .blend not at {blend_path}; this test requires "
            f"the AYON corpus to be available.",
            file=sys.stderr,
        )
        return 0
    else:
        bpy.ops.wm.open_mainfile(filepath=blend_path)

    if "creature-body" not in bpy.data.materials:
        print(
            f"SKIP: swarmfish.blend at {blend_path} is missing `creature-body`; "
            f"the file may have changed or this isn't the version the diagnostic "
            f"was run against.",
            file=sys.stderr,
        )
        return 0

    export_path = os.path.join(
        tempfile.gettempdir(), "test_swarmfish_materialx_dangling_refs.usda"
    )

    result = bpy.ops.wm.usd_export(
        filepath=export_path,
        export_materials=True,
        generate_materialx_network=True,
        generate_preview_surface=True,
        selected_objects_only=False,
        evaluation_mode="RENDER",
    )

    if "FINISHED" not in result:
        print(f"FAIL: USD export did not finish cleanly: {result}", file=sys.stderr)
        return 1

    if not os.path.isfile(export_path):
        print(f"FAIL: export did not produce {export_path}", file=sys.stderr)
        return 1

    print(f"EXPORTED_USDA={export_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
