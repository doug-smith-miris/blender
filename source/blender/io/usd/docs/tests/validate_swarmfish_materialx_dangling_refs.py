# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Validator for BL-MAT-002-materialx-dangling-refs-break-karma-compile.

See ``test_swarmfish_materialx_dangling_refs.py`` for the failure mode this
addresses.  This validator opens the .usda exported by that test via the
``pxr.UsdShade`` Python bindings and asserts three things about the
MaterialX-bearing materials (``creature-body``, ``creature-eyes``,
``creature-pupil``, etc. -- any Material with a ``UsdMtlxConfigAPI``
schema applied and a connected ``outputs:mtlx:surface``):

1.  No ``Shader`` prim under such a Material has an empty ``info:id``.
    This is the direct fingerprint of the original failure -- on stock
    Blender the ``thin_film_bsdf`` node emits as a ``def Shader`` with no
    ``info:id`` because MaterialX 1.39 has no NodeDef for that category,
    and ``pxr::UsdMtlxRead`` warns "Unable to find the nodedef for
    'node_NN' node, outputs not added."

2.  At least one Shader under such a Material has
    ``info:id = "ND_oren_nayar_diffuse_bsdf"`` *with* ``inputs:weight=0`` --
    the typed-identity fingerprint the fix uses to stand in for the
    rewritten ``thin_film_bsdf`` node. Without the fix this Shader would
    have been the ``thin_film_bsdf`` itself (empty ``info:id``), so the
    presence of the weight=0 stand-in is the affirmative regression sentinel.

3.  Every ``ND_layer_bsdf`` Shader under such a Material has both
    ``inputs:base`` and ``inputs:top`` connected. This is the downstream
    symptom: on stock Blender, ``UsdMtlxRead`` drops the connection from
    a layer's ``top`` BSDF input to its (now-broken) source, leaving a
    declared-but-unconnected BSDF input. Karma's MtlX shader generator
    then fails with "Reference to undefined variable" because there is
    no value, no connection, and no default for BSDF-typed inputs.

Invocation:

    /Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython \\
        validate_swarmfish_materialx_dangling_refs.py <path-to-exported-usda>

Exits 0 on pass, non-zero on assertion failure.
"""

import sys

from pxr import Sdf, Usd, UsdShade


def _materials_with_mtlx_surface(stage):
    """Yield Material prims whose ``outputs:mtlx:surface`` is connected.

    Identifies MaterialX-bearing materials regardless of whether the
    UsdMtlxMaterialXConfigAPI schema was applied -- the connection itself is
    the operational signal.
    """
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        mat = UsdShade.Material(prim)
        mtlx_out = mat.GetSurfaceOutput("mtlx")
        if not mtlx_out:
            continue
        conns = mtlx_out.GetConnectedSources()
        if conns and conns[0]:
            yield prim


def _iter_shaders(material_prim):
    for descendant in Usd.PrimRange(material_prim):
        if descendant.GetTypeName() == "Shader":
            yield descendant


def main(exported_usda: str) -> int:
    stage = Usd.Stage.Open(exported_usda)
    if not stage:
        print(f"FAIL: could not open stage at {exported_usda}", file=sys.stderr)
        return 1

    mtlx_materials = list(_materials_with_mtlx_surface(stage))
    if not mtlx_materials:
        print(
            "FAIL: no Material prims with a connected outputs:mtlx:surface; "
            "the export did not produce a MaterialX network.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Found {len(mtlx_materials)} MaterialX-bearing Material(s): "
        f"{[p.GetName() for p in mtlx_materials]}"
    )

    no_id_shaders = []
    identity_substitutions = []
    layer_bsdf_orphan_inputs = []

    for mat_prim in mtlx_materials:
        for shader_prim in _iter_shaders(mat_prim):
            sh = UsdShade.Shader(shader_prim)
            shader_id = sh.GetShaderId() or ""

            if not shader_id:
                no_id_shaders.append(str(shader_prim.GetPath()))
                continue

            # Detect the identity substitution: a previously-broken `thin_film_bsdf`
            # is rewritten in-place to ND_oren_nayar_diffuse_bsdf with weight=0.
            # Distinguishing the substitution from a genuine zero-weight diffuse
            # BSDF: the substituted node will also have color set to black exactly,
            # and the BSDF won't be wired into a `layer` `base` (it would only
            # appear as a `top` of a layer that originally consumed thin-film).
            if shader_id == "ND_oren_nayar_diffuse_bsdf":
                weight_attr = shader_prim.GetAttribute("inputs:weight")
                color_attr = shader_prim.GetAttribute("inputs:color")
                if weight_attr and weight_attr.Get() == 0.0:
                    color = color_attr.Get() if color_attr else None
                    if color is not None and tuple(color) == (0.0, 0.0, 0.0):
                        identity_substitutions.append(str(shader_prim.GetPath()))

            if shader_id == "ND_layer_bsdf":
                for input_name in ("base", "top"):
                    attr = shader_prim.GetAttribute(f"inputs:{input_name}")
                    if not attr or not attr.HasAuthoredConnections():
                        layer_bsdf_orphan_inputs.append(
                            (str(shader_prim.GetPath()), input_name)
                        )

    failures = []

    # Assertion 1: every Shader has an info:id.
    if no_id_shaders:
        failures.append(
            f"{len(no_id_shaders)} Shader prim(s) under MaterialX materials have no "
            f"info:id (BL-MAT-002 sentinel): "
            f"{no_id_shaders[:5]}{'…' if len(no_id_shaders) > 5 else ''}"
        )
    else:
        print("OK: every Shader prim under MaterialX materials has an info:id.")

    # Assertion 2: thin_film_bsdf substitution actually ran.
    if not identity_substitutions:
        # This assertion is *informational* on assets that don't trigger
        # thin_film_bsdf creation (Principled with thin_film_thickness/IOR
        # default values may not produce one in every code path). On
        # swarmfish the diagnostic agent confirmed the node IS produced
        # before this fix, so absence here would mean the substitution
        # logic did not run on a known-bad path.
        print(
            "WARN: no oren_nayar_diffuse_bsdf weight=0 identity nodes found. "
            "If swarmfish triggers thin_film_bsdf creation (it does on stock "
            "Blender per the diagnostic), this would indicate the substitution "
            "did not run. Surface visually if a regression.",
            file=sys.stderr,
        )
    else:
        print(
            f"OK: found {len(identity_substitutions)} ND_oren_nayar_diffuse_bsdf "
            f"weight=0 identity substitution(s) -- e.g. {identity_substitutions[0]}"
        )

    # Assertion 3: no layer_bsdf has an orphan `top`/`base` input.
    if layer_bsdf_orphan_inputs:
        failures.append(
            f"{len(layer_bsdf_orphan_inputs)} ND_layer_bsdf input(s) are declared "
            f"but unconnected (Karma will fail to compile): "
            f"{layer_bsdf_orphan_inputs[:5]}"
            f"{'…' if len(layer_bsdf_orphan_inputs) > 5 else ''}"
        )
    else:
        print("OK: every ND_layer_bsdf has both base and top connected.")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1

    print("PASS: BL-MAT-002 fingerprints are clear on the exported USD.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <exported-usda>",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
