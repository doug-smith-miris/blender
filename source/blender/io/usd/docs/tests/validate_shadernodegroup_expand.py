# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
USD-side validation for ShaderNodeGroup expansion in the UsdPreviewSurface
writer (BL-MAT-NG-001).

Opens the .usda produced by ``test_shadernodegroup_expand.py`` and asserts:

  1. A `UsdShade.Material` named `GroupWrappedMaterial` exists.
  2. The Material's `surface` output is connected (not empty).
  3. A `UsdPreviewSurface` shader prim exists under the material.
  4. The shader's `diffuseColor` input has a meaningful value, matching the
     iris-pink the inner ShaderNodeRGB drove.
  5. The shader's `roughness` input matches the inner ShaderNodeValue (0.42).

Before the fix, find_bsdf_node returned null for any material whose Principled
BSDF lived inside a ShaderNodeGroup, and `create_usd_preview_surface_material`
returned early — producing a Material prim with no shader graph at all. The
checks above all fail on the buggy exporter.

Invocation:

    /Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython \\
        source/blender/io/usd/docs/tests/validate_shadernodegroup_expand.py \\
        /tmp/test_shadernodegroup_expand.usda

Exits 0 on success, 1 on any check failure.
"""

import sys

from pxr import Usd, UsdShade


PREVIEW_SURFACE_ID = "UsdPreviewSurface"
EXPECTED_DIFFUSE = (0.85, 0.20, 0.30)
EXPECTED_ROUGHNESS = 0.42
TOLERANCE = 1e-4


def fail(msg: str) -> None:
    print(f"VALIDATE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def find_material(stage, name):
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material) and prim.GetName() == name:
            return UsdShade.Material(prim)
    return None


def find_preview_surface_shader(material):
    for prim in Usd.PrimRange(material.GetPrim()):
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        if shader.GetIdAttr().Get() == PREVIEW_SURFACE_ID:
            return shader
    return None


def approx_equal(a, b, tol=TOLERANCE):
    return abs(float(a) - float(b)) <= tol


def main() -> None:
    if len(sys.argv) < 2:
        fail("Usage: hython validate_shadernodegroup_expand.py <path-to.usda>")

    usda_path = sys.argv[1]
    stage = Usd.Stage.Open(usda_path)
    if not stage:
        fail(f"Could not open USD stage at {usda_path}")

    material = find_material(stage, "GroupWrappedMaterial")
    if material is None:
        fail("Material `GroupWrappedMaterial` not found in stage")

    print(f"Material prim: {material.GetPath()}")

    surface_out = material.GetSurfaceOutput()
    if not surface_out or not surface_out.HasConnectedSource():
        fail(
            "Material's `surface` output is unconnected — the exporter emitted "
            "an empty Material prim, which is the BL-MAT-NG-001 failure mode "
            "(ShaderNodeGroup wrappers cause find_bsdf_node to return null)."
        )

    src = UsdShade.ConnectableAPI.GetConnectedSource(surface_out)
    print(f"surface connected to: {src[0].GetPath()}.{src[1]}")

    preview_shader = find_preview_surface_shader(material)
    if preview_shader is None:
        fail(
            "No UsdPreviewSurface shader prim found under the material. "
            "This means the BSDF inside the ShaderNodeGroup was not "
            "expanded by the exporter."
        )

    print(f"UsdPreviewSurface shader: {preview_shader.GetPath()}")

    # diffuseColor: should match the inner RGB constant (iris pink).
    diffuse_input = preview_shader.GetInput("diffuseColor")
    if diffuse_input is None:
        fail("UsdPreviewSurface shader is missing `diffuseColor` input")

    # The input may be literal (value Set) or connected to an upstream shader
    # (e.g. an Image Texture). Either is fine — we just need it to be
    # meaningful, not the default 0.18/0.18/0.18.
    if diffuse_input.HasConnectedSource():
        src = UsdShade.ConnectableAPI.GetConnectedSource(diffuse_input)
        print(f"diffuseColor connected to: {src[0].GetPath()}.{src[1]}")
    else:
        val = diffuse_input.Get()
        if val is None:
            fail("diffuseColor has no value and no connection")
        if (
            approx_equal(val[0], EXPECTED_DIFFUSE[0])
            and approx_equal(val[1], EXPECTED_DIFFUSE[1])
            and approx_equal(val[2], EXPECTED_DIFFUSE[2])
        ):
            print(f"diffuseColor = {tuple(val)} (matches inner RGB)")
        else:
            fail(
                f"diffuseColor = {tuple(val)} does not match expected "
                f"{EXPECTED_DIFFUSE} from the inner ShaderNodeRGB. The BSDF "
                f"was found but its inputs were not traced into the group."
            )

    # roughness: should match the inner ShaderNodeValue (0.42).
    roughness_input = preview_shader.GetInput("roughness")
    if roughness_input is None:
        fail("UsdPreviewSurface shader is missing `roughness` input")

    if roughness_input.HasConnectedSource():
        src = UsdShade.ConnectableAPI.GetConnectedSource(roughness_input)
        print(f"roughness connected to: {src[0].GetPath()}.{src[1]}")
    else:
        rval = roughness_input.Get()
        if rval is None:
            fail("roughness has no value and no connection")
        if approx_equal(rval, EXPECTED_ROUGHNESS):
            print(f"roughness = {rval} (matches inner Value)")
        else:
            fail(
                f"roughness = {rval} does not match expected "
                f"{EXPECTED_ROUGHNESS} from the inner ShaderNodeValue."
            )

    print("VALIDATE OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
