# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration test for BL-MAT-NG-001 against the actual swarmfish.blend asset
that surfaced the finding (pipeline-runs/edefbd0e-499d-4d13-b3e0-1d2418b96960).

The synthetic test in ``test_shadernodegroup_expand.py`` covers the
``find_bsdf_node`` recursion in isolation. This test drives swarmfish.blend
directly and asserts that the two materials documented as broken
(``creature-eyes`` and ``creature-pupil``) come out of the exporter as
non-empty Material prims with a UsdPreviewSurface shader bound to ``surface``.

This test requires the AYON corpus on disk. If the .blend file isn't present
the script exits 0 with a skip message — that keeps it benign in
environments that don't have the corpus mounted.

Invocation:

    /Applications/Blender.app/Contents/MacOS/Blender \\
        --background <path-to-swarmfish-v001.blend> \\
        --python source/blender/io/usd/docs/tests/test_swarmfish_shadernodegroup_expand.py

Prints ``EXPORTED_USDA=<path>`` for the validation step
(``validate_swarmfish_shadernodegroup_expand.py``).
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
        # blender --background <blend> --python <this> already opened the file.
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

    # Confirm the two target materials exist in the file; otherwise the
    # asset isn't what the finding documents.
    required = {"creature-eyes", "creature-pupil"}
    found = {m.name for m in bpy.data.materials if m.name in required}
    missing = required - found
    if missing:
        print(
            f"SKIP: swarmfish.blend at {blend_path} is missing required "
            f"materials {sorted(missing)}; the file may have changed or this "
            f"isn't the version the diagnostic was run against.",
            file=sys.stderr,
        )
        return 0

    export_path = os.path.join(
        tempfile.gettempdir(), "test_swarmfish_shadernodegroup_expand.usda"
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
