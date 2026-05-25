# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
USD-side validation partner to ``test_swarmfish_shadernodegroup_expand.py``.

Opens the .usda the swarmfish test produced and asserts that the two materials
documented as broken in
``pipeline-runs/edefbd0e-499d-4d13-b3e0-1d2418b96960/discovered-context.md``
(``creature-eyes`` and ``creature-pupil``) emit a UsdPreviewSurface shader
under their Material prim with the surface output connected.

Before the fix these prims came out as ``def Material "creature-eyes" {
custom string userProperties:blender:data_name = "creature-eyes" }`` — no
shader graph at all, because ``find_bsdf_node`` only walks the top-level node
tree and the Principled BSDF lives inside ``SH-creature-eye``.

Invocation:

    /Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython \\
        source/blender/io/usd/docs/tests/validate_swarmfish_shadernodegroup_expand.py \\
        /tmp/test_swarmfish_shadernodegroup_expand.usda

Exits 0 on success, 1 on validation failure.
"""

import sys

from pxr import Usd, UsdShade


TARGET_MATERIALS = ("creature-eyes", "creature-pupil")
PREVIEW_SURFACE_ID = "UsdPreviewSurface"


def fail(msg: str) -> None:
    print(f"VALIDATE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def find_material(stage, name):
    """Locate a material by its original Blender name.

    USD identifier sanitization rewrites ``creature-eyes`` to ``creature_eyes``
    on the prim, so matching on prim name alone misses these. The original
    Blender name is preserved on ``userProperties:blender:data_name``; match
    that first and fall back to prim name for safety.
    """
    fallback = None
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Material):
            continue
        data_name_attr = prim.GetAttribute("userProperties:blender:data_name")
        if data_name_attr and data_name_attr.Get() == name:
            return UsdShade.Material(prim)
        if prim.GetName() == name:
            fallback = UsdShade.Material(prim)
    return fallback


def find_preview_surface_shader(material):
    for prim in Usd.PrimRange(material.GetPrim()):
        if prim.IsA(UsdShade.Shader):
            shader = UsdShade.Shader(prim)
            if shader.GetIdAttr().Get() == PREVIEW_SURFACE_ID:
                return shader
    return None


def main() -> None:
    if len(sys.argv) < 2:
        fail("Usage: hython validate_swarmfish_shadernodegroup_expand.py <path-to.usda>")

    usda_path = sys.argv[1]
    stage = Usd.Stage.Open(usda_path)
    if not stage:
        fail(f"Could not open USD stage at {usda_path}")

    failures = []
    for name in TARGET_MATERIALS:
        material = find_material(stage, name)
        if material is None:
            failures.append(f"material `{name}` not found in stage")
            continue

        print(f"Material {material.GetPath()}")

        surface_out = material.GetSurfaceOutput()
        if not surface_out or not surface_out.HasConnectedSource():
            failures.append(
                f"`{name}`: surface output is unconnected — empty Material prim "
                f"(BL-MAT-NG-001 failure mode)"
            )
            continue

        src = UsdShade.ConnectableAPI.GetConnectedSource(surface_out)
        print(f"  surface connected to: {src[0].GetPath()}.{src[1]}")

        preview_shader = find_preview_surface_shader(material)
        if preview_shader is None:
            failures.append(
                f"`{name}`: no UsdPreviewSurface shader prim found under the material"
            )
            continue

        print(f"  UsdPreviewSurface shader: {preview_shader.GetPath()}")

    if failures:
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        fail(f"{len(failures)} material(s) failed validation")

    print("VALIDATE OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
