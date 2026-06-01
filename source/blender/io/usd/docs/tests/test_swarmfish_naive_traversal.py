# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration test for BL-MAT-001-wrong-texture-wired-by-naive-traversal.

The original ``traverse_channel`` in usd_writer_material.cc walked the upstream
node graph greedily and returned the first ``ShaderNodeTexImage`` it found
through ANY input of every intermediate node. On swarmfish's ``creature-body``
material the Principled BSDF's Base Color goes through Hue/Saturation → Mix →
Math chains where one branch hits the roughness EXR — so diffuseColor (and
emissiveColor and normal) ended up connected to the WRONG texture upstream of
the actual color source.

Fix: traverse_channel now consults a per-input allowlist (`is_value_carrier_input`)
to decide which input sockets carry the value through to the output, refusing
to descend into modulating sockets like Fac / Hue / From Min / etc.

This integration test drives swarmfish-v001.blend and asks the validator to
confirm that diffuseColor, roughness, normal, emissive on ``creature-body``
are no longer cross-wired into the same wrong texture.

Invocation:

    /Users/d.smith/MirisProjects/build_darwin/bin/Blender.app/Contents/MacOS/Blender \\
        --background <path-to-swarmfish-v001.blend> \\
        --python source/blender/io/usd/docs/tests/test_swarmfish_naive_traversal.py

Prints ``EXPORTED_USDA=<path>`` for the validation step
(``validate_swarmfish_naive_traversal.py``).
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
        tempfile.gettempdir(), "test_swarmfish_naive_traversal.usda"
    )

    result = bpy.ops.wm.usd_export(
        filepath=export_path,
        export_materials=True,
        generate_materialx_network=False,
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
