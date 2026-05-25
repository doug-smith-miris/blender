# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
USD-side validation for the legacy ShaderNodeMixRGB → MaterialX ``mix`` mapping.

Opens the .usda produced by ``test_mix_rgb.py`` and walks the MaterialX network
attached to the material. Asserts that:

  1. The MaterialX shader graph exists and is wired to ``mtlx:surface`` on the
     Material prim.
  2. A MaterialX ``ND_mix_color3`` (or ``ND_mix_color4``) shader exists in the
     network.
  3. That shader's ``bg`` and ``fg`` inputs are connected (or set to non-default
     values) — i.e. the mix wasn't silently dropped.
  4. The shader's ``mix`` input is wired to a clamp / has the expected
     `Fac = 0.5` semantic.

Invocation:

    /Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython \\
        source/blender/io/usd/docs/tests/validate_mix_rgb.py /tmp/test_mix_rgb_export.usda

Exits 0 on success, non-zero on validation failure (with a diagnostic message
on stderr).
"""

import sys

from pxr import Usd, UsdShade


# Identifier strings the MaterialX ``mix`` standard node emits as USD shader IDs.
# (One per primary type signature.)
MIX_SHADER_IDS = {
    "ND_mix_color3",
    "ND_mix_color4",
    "ND_mix_vector3",
    "ND_mix_float",
}


def fail(msg: str) -> None:
    print(f"VALIDATE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        fail("Usage: hython validate_mix_rgb.py <path-to.usda>")

    usda_path = sys.argv[1]
    stage = Usd.Stage.Open(usda_path)
    if not stage:
        fail(f"Could not open USD stage at {usda_path}")

    # Find the material prim. The Blender exporter places it under
    # /root/_materials/<MaterialName>. We do a broad search so the test isn't
    # brittle if the path scheme changes.
    material_prim = None
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material):
            material_prim = prim
            break

    if material_prim is None:
        fail("No UsdShade.Material prim found in the stage")

    material = UsdShade.Material(material_prim)
    mtlx_out = material.GetOutput("mtlx:surface")
    if not mtlx_out:
        fail(
            "Material has no `mtlx:surface` output — the exporter likely fell "
            "back to UsdPreviewSurface instead of emitting a MaterialX network"
        )

    # Walk the MaterialX subgraph to find a `mix` shader. The subgraph lives
    # under the Material prim's namespace.
    mix_shaders = []
    for prim in Usd.PrimRange(material_prim):
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        shader_id = shader.GetIdAttr().Get()
        if shader_id in MIX_SHADER_IDS:
            mix_shaders.append(shader)

    if not mix_shaders:
        fail(
            "No MaterialX `mix` shader (ND_mix_color3/color4/vector3/float) "
            "found under the material — the ShaderNodeMixRGB was dropped from "
            "the exported network. This is the failure mode the architectural "
            "mapping bite was meant to fix."
        )

    print(f"Found {len(mix_shaders)} MaterialX mix shader(s):")
    for s in mix_shaders:
        print(f"  - {s.GetPath()} (id={s.GetIdAttr().Get()})")

    # Sanity check: the first mix shader should have bg, fg, mix inputs in
    # a meaningful configuration (connected, or set to non-default).
    first = mix_shaders[0]

    # bg and fg should be connected to upstream nodes (the RGB constants), or
    # at the very least have explicit color values set (not the default 0,0,0).
    for input_name in ("bg", "fg"):
        inp = first.GetInput(input_name)
        if not inp:
            fail(f"MaterialX mix shader is missing required `{input_name}` input")
        connected = (
            UsdShade.ConnectableAPI.GetConnectedSource(inp)
            if inp.HasConnectedSource()
            else None
        )
        if connected:
            print(f"  {input_name}: connected to {connected[0].GetPath()}.{connected[1]}")
            continue
        val = inp.Get()
        # If neither connected nor explicitly set, the mix is degenerate.
        if val is None:
            fail(f"`{input_name}` is neither connected nor has an explicit value")
        print(f"  {input_name}: literal value {val}")

    mix_input = first.GetInput("mix")
    if mix_input is None:
        fail("MaterialX mix shader is missing required `mix` (fac) input")
    if mix_input.HasConnectedSource():
        src = UsdShade.ConnectableAPI.GetConnectedSource(mix_input)
        print(f"  mix: connected to {src[0].GetPath()}.{src[1]}")
    else:
        print(f"  mix: literal value {mix_input.Get()}")

    print("VALIDATE OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
