# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
USD-side validation for the MaterialX RGB Curves translation
(finding BL-MAT-005-rgbcurves-silent-passthrough).

Opens the .usda produced by ``test_rgbcurves_passthrough.py`` and asserts:

  1. A `UsdShade.Material` named `RGBCurvesMaterial` exists with a
     MaterialX-side surface output (`outputs:mtlx:surface`).
  2. The MaterialX node-graph contains a shader named `bnode__RGB_Curves`,
     and that shader's `info:id` is NOT one of the silent-passthrough no-op
     identifiers (`ND_constant_color3`, `ND_convert_color4_color3`) — these
     are the fingerprints the diagnostic agent recorded for this finding.
  3. The network downstream of the curve includes at least one of each:
       - `ND_combine3_color3` (channels were processed independently)
       - `ND_ifgreatereq_float` (piecewise-linear segment selection)
       - `ND_extract_color3` (per-channel extraction from input Color)
     i.e. the curve evaluation was actually emitted as a MaterialX network
     rather than dropped on the floor.

Before the fix, ``rgb::node_shader_materialx`` returned
``get_input_value("Color", Color3)`` unchanged, and the MaterialX writer's
constant-folding collapsed the entire RGB Curves output to a single
``ND_constant_color3`` (or ``ND_convert_color4_color3`` when the input
chain forced a type coercion). All three assertions above fail on that
output.

Invocation:

    /Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython \\
        source/blender/io/usd/docs/tests/validate_rgbcurves_passthrough.py \\
        /tmp/test_rgbcurves_passthrough.usda

Exits 0 on success, 1 on any check failure.
"""

import sys

from pxr import Usd, UsdShade


PASSTHROUGH_IDS = {"ND_constant_color3", "ND_convert_color4_color3"}
REQUIRED_NODE_IDS = {
    "ND_combine3_color3",
    "ND_ifgreatereq_float",
    "ND_extract_color3",
}


def fail(msg: str) -> None:
    print(f"VALIDATE FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def find_material(stage, name):
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material) and prim.GetName() == name:
            return UsdShade.Material(prim)
    return None


def collect_shaders(material):
    """Return list of (path, info_id) for every UsdShade.Shader under the
    material prim (including descendants of any NodeGraph beneath it)."""
    shaders = []
    for prim in Usd.PrimRange(material.GetPrim()):
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        info_id = shader.GetIdAttr().Get()
        shaders.append((str(prim.GetPath()), info_id))
    return shaders


def main() -> None:
    if len(sys.argv) < 2:
        fail("Usage: hython validate_rgbcurves_passthrough.py <path-to.usda>")

    usda_path = sys.argv[1]
    stage = Usd.Stage.Open(usda_path)
    if not stage:
        fail(f"Could not open USD stage at {usda_path}")

    material = find_material(stage, "RGBCurvesMaterial")
    if material is None:
        fail("Material `RGBCurvesMaterial` not found in stage")
    print(f"Material prim: {material.GetPath()}")

    # The MaterialX-side surface output is the path under test (the
    # UsdPreviewSurface output is exported through a separate code path
    # that already worked).
    mtlx_surface = material.GetPrim().GetAttribute("outputs:mtlx:surface")
    if not mtlx_surface or not mtlx_surface.HasAuthoredConnections():
        fail("Material lacks an authored `outputs:mtlx:surface` connection.")

    shaders = collect_shaders(material)
    if not shaders:
        fail("No UsdShade.Shader prims found beneath the material.")

    rgb_curves_shader_id = None
    for path, info_id in shaders:
        if path.endswith("/bnode__RGB_Curves"):
            rgb_curves_shader_id = info_id
            break
    if rgb_curves_shader_id is None:
        fail(
            "Could not locate a shader named `bnode__RGB_Curves` under the "
            "material — was the RGB Curves node emitted at all?"
        )
    print(f"bnode__RGB_Curves info:id = {rgb_curves_shader_id}")

    if rgb_curves_shader_id in PASSTHROUGH_IDS:
        fail(
            f"bnode__RGB_Curves was emitted as `{rgb_curves_shader_id}`, which "
            f"is the BL-MAT-005 silent-passthrough fingerprint. The curve was "
            f"dropped on export."
        )

    info_ids_in_graph = {info_id for _, info_id in shaders}
    missing = REQUIRED_NODE_IDS - info_ids_in_graph
    if missing:
        fail(
            "MaterialX node-graph is missing expected curve-network shaders: "
            f"{sorted(missing)}. Present IDs: {sorted(info_ids_in_graph)}"
        )

    # Surface-level sanity check: count ifgreatereq nodes. With N=8 segments
    # and 3 non-identity channels (R and G non-identity in the test; B is
    # identity), the writer should emit roughly 7 ifgreatereq nodes per
    # non-identity channel = 14 total. The exact count is implementation-
    # defined, but anything less than ~5 means the PWL fold didn't run.
    n_ifgreatereq = sum(
        1 for _, info_id in shaders if info_id == "ND_ifgreatereq_float"
    )
    if n_ifgreatereq < 5:
        fail(
            f"Only {n_ifgreatereq} ND_ifgreatereq_float shaders found — the "
            "piecewise-linear curve fold did not run."
        )
    print(f"ifgreatereq node count: {n_ifgreatereq}")

    print("VALIDATE OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
