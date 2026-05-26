# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
USD-side validation partner to ``test_swarmfish_naive_traversal.py``.

Opens the exported .usda and asserts that ``creature-body`` no longer has its
roughness EXR mis-wired into diffuseColor / emissiveColor (the failure mode
documented in pipeline-runs/edefbd0e-499d-4d13-b3e0-1d2418b96960/swarmfish_preview.usda
where all three channels pointed at ``roughness-watercolor.exr``).

Specifically, before the fix the export looked like:

    def Material "creature_body"
    {
        def Shader "Principled_BSDF_002"
        {
            color3f inputs:diffuseColor.connect = Image_Texture_002.outputs:rgb  # roughness EXR
            color3f inputs:emissiveColor.connect = Image_Texture_002.outputs:rgb # roughness EXR
            normal3f inputs:normal.connect = Image_Texture.outputs:rgb           # color png
            float inputs:roughness.connect = Image_Texture_002.outputs:r         # roughness EXR
        }
    }

After the fix the channel-aware traversal will not return the roughness EXR
for the Base Color / Emission Color sockets, since their upstream chain goes
Hue/Saturation → Mix → Math (no value-carrying path to that texture). The
Image Texture _is_ on the Normal chain (Bump → RGB Curves → Image Texture), so
``normal`` may still connect; the test asserts only the cross-contamination
into diffuse/emissive is gone.

Invocation:

    /Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython \\
        source/blender/io/usd/docs/tests/validate_swarmfish_naive_traversal.py \\
        /tmp/test_swarmfish_naive_traversal.usda

Exits 0 on success, 1 on validation failure.
"""

import os
import sys

from pxr import Sdf, Usd, UsdShade


TARGET_MATERIAL = "creature-body"
ROUGHNESS_TEX_HINT = "roughness"  # roughness-watercolor.exr
COLOR_TEX_HINT = "watercolor_blended"  # the actual base-color texture


def fail(msg: str) -> None:
    print(f"VALIDATE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def find_material(stage, name):
    """Match material by Blender data_name (sanitization rewrites ``-`` to ``_``)."""
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
            if shader.GetIdAttr().Get() == "UsdPreviewSurface":
                return shader
    return None


def resolve_texture_file(stage, shader_input):
    """Follow an input's connection back to a UsdUVTexture and return its file asset path."""
    if not shader_input or not shader_input.HasConnectedSource():
        return None
    src = UsdShade.ConnectableAPI.GetConnectedSource(shader_input)
    if not src:
        return None
    src_prim = src[0].GetPrim()
    if not src_prim.IsA(UsdShade.Shader):
        return None
    src_shader = UsdShade.Shader(src_prim)
    if src_shader.GetIdAttr().Get() != "UsdUVTexture":
        return None
    file_input = src_shader.GetInput("file")
    if not file_input:
        return None
    val = file_input.Get()
    if val is None:
        return None
    # SdfAssetPath -> resolvedPath/path string
    asset_path = val.path if isinstance(val, Sdf.AssetPath) else str(val)
    return asset_path


def main() -> None:
    if len(sys.argv) < 2:
        fail("Usage: hython validate_swarmfish_naive_traversal.py <path-to.usda>")

    usda_path = sys.argv[1]
    stage = Usd.Stage.Open(usda_path)
    if not stage:
        fail(f"Could not open USD stage at {usda_path}")

    material = find_material(stage, TARGET_MATERIAL)
    if material is None:
        fail(f"material `{TARGET_MATERIAL}` not found in stage")

    print(f"Material {material.GetPath()}")

    shader = find_preview_surface_shader(material)
    if shader is None:
        fail(f"`{TARGET_MATERIAL}`: no UsdPreviewSurface shader prim found")

    print(f"  UsdPreviewSurface shader: {shader.GetPath()}")

    diffuse_tex = resolve_texture_file(stage, shader.GetInput("diffuseColor"))
    emissive_tex = resolve_texture_file(stage, shader.GetInput("emissiveColor"))
    roughness_tex = resolve_texture_file(stage, shader.GetInput("roughness"))
    normal_tex = resolve_texture_file(stage, shader.GetInput("normal"))

    print(f"  diffuseColor   -> {diffuse_tex}")
    print(f"  emissiveColor  -> {emissive_tex}")
    print(f"  roughness      -> {roughness_tex}")
    print(f"  normal         -> {normal_tex}")

    failures = []

    # Cross-contamination check: diffuseColor MUST NOT point at the roughness EXR.
    if diffuse_tex and ROUGHNESS_TEX_HINT in os.path.basename(diffuse_tex).lower():
        failures.append(
            f"diffuseColor is wired to {diffuse_tex!r} which contains "
            f"'{ROUGHNESS_TEX_HINT}' — this is the naive-traversal bug "
            f"(BL-MAT-001-wrong-texture-wired-by-naive-traversal)."
        )

    # Cross-contamination check: emissiveColor MUST NOT point at the roughness EXR.
    if emissive_tex and ROUGHNESS_TEX_HINT in os.path.basename(emissive_tex).lower():
        failures.append(
            f"emissiveColor is wired to {emissive_tex!r} which contains "
            f"'{ROUGHNESS_TEX_HINT}' — naive traversal is still picking up "
            f"the wrong texture from a sibling branch."
        )

    if failures:
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        fail(f"{len(failures)} cross-contamination check(s) failed")

    print("VALIDATE OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
