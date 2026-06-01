"""Real-asset regression test for `BL-MAT-OPACITY-LIGHTPATH-DROP`.

Refinement of the PR #27 (`bl-usd-alpha-chain-zeroed:creature-body`) fix: when the
Principled BSDF Alpha / Transmission Weight socket is linked to a Light Path /
Transparent BSDF *visibility cutout*, the writer correctly authors
`UsdPreviewSurface.opacity = 1.0` (the surface IS visible to the camera). But
opacity = 1.0 throws away the second half of the cutout's intent: "do not
contribute to secondary rays" -- most often, "do not cast a shadow". Bare
opacity=1 starts dropping solid shadows the artist never asked Cycles to evaluate
(critter_tongue would otherwise silhouette critter_body's chest).

The fix in usd_writer_material.cc::process_inputs additionally authors

    string primvars:karma:object:rendervisibility = "camera,reflect,refract,diffuse,glossy,volume"

on the Material prim whenever the opacity socket's chain matches the Light Path /
Transparent BSDF cutout topology (existing `opacity_socket_drives_visibility_cutout`
helper). The visibility list excludes "shadow", so Karma drops the bound geometry
from shadow rays while leaving primary / reflect / refract / diffuse / glossy
visibility intact. Renderers that do not consult this Karma-namespaced primvar
ignore it.

Regression sentinel (critter-v001.blend, body_purple + body_mouth_bag materials --
these are the *actual* critter materials whose opacity sockets are linked to a
Light-Path / Transparent-BSDF cutout; the bite-hint's `critter_tongue` is in fact
a constant-opacity painterly surface with no cutout):
  - STOCK   /Applications/Blender.app : no `primvars:karma:object:rendervisibility`
                                        on body_purple / body_mouth_bag -> tongue
                                        interior casts a solid shadow on
                                        critter_body in Karma -> test FAILS.
  - PATCHED build_darwin              : primvar authored, "shadow" NOT in value
                                        -> test PASSES.

Additionally, the PR #27 invariant must hold (creature_body still authors
opacity=1.0); this test exercises the same code path on critter so the swarmfish
test (test_miris_bl_usd_alpha_chain_zeroed_creature_body.py) stays green.

Run headless with the PATCHED build:
  build_darwin/.../Blender --background <critter-v001.blend> \
      --python test_miris_bl_mat_opacity_lightpath_drop.py
"""

import os
import sys
import tempfile

import bpy
from pxr import Usd, UsdShade

CRITTER_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/critter/publish/critter-v001.blend"
)

KARMA_VIS_ATTR = "primvars:karma:object:rendervisibility"


def export_critter_preview():
    out_dir = tempfile.mkdtemp(prefix="miris_opacity_lightpath_drop_")
    usd_path = os.path.join(out_dir, "critter-preview.usda")

    # Unhide everything so geometry-nodes meshes (and their materials) survive eval.
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
        generate_materialx_network=False,
        root_prim_path="/root",
    )
    return usd_path


def find_material(stage, name):
    for prim in stage.Traverse():
        mat = UsdShade.Material(prim)
        if mat and prim.GetName() == name:
            return mat
    return None


def find_preview_surface(material):
    for child in material.GetPrim().GetChildren():
        shader = UsdShade.Shader(child)
        if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
            return shader
    return None


def check_material(stage, name, *, require_rendervisibility):
    """Returns a list of failures for the named material.

    `require_rendervisibility=True` enforces the new bite invariant (primvar
    authored, "shadow" excluded). `False` is used as a soft check on adjacent
    materials -- they may or may not carry a Light-Path cutout.
    """
    failures = []
    material = find_material(stage, name)
    if material is None:
        return [f"{name}: Material prim missing entirely"]

    surface = find_preview_surface(material)
    if surface is None:
        return [f"{name}: no UsdPreviewSurface shader -- cannot validate opacity"]

    opacity_input = surface.GetInput("opacity")
    if opacity_input is None:
        failures.append(f"{name}: UsdPreviewSurface has no opacity input")
    else:
        connected = bool(opacity_input.GetConnectedSources()[0])
        opacity_val = opacity_input.Get()
        print(f"MIRIS_{name}_OPACITY connected={connected} value={opacity_val}")
        if connected:
            failures.append(
                f"{name}: opacity is connected to a shader source -- a Light-Path/"
                "Transparent cutout is not representable as a UsdPreviewSurface "
                "opacity texture"
            )
        if opacity_val is None:
            failures.append(f"{name}: opacity has no authored constant value")
        elif abs(float(opacity_val) - 1.0) > 1e-6:
            failures.append(
                f"{name}: opacity == {float(opacity_val)} -- expected 1.0 (fully "
                f"opaque) for an unrepresentable Light-Path visibility cutout (PR #27)"
            )

    if require_rendervisibility:
        prim = material.GetPrim()
        attr = prim.GetAttribute(KARMA_VIS_ATTR)
        if not attr or not attr.IsAuthored():
            failures.append(
                f"{name}: `{KARMA_VIS_ATTR}` not authored on Material prim -- "
                "the Light-Path cutout's no-shadow intent is being dropped, so "
                "the bound geometry casts a solid shadow it never cast under Cycles"
            )
        else:
            val = attr.Get()
            print(f"MIRIS_{name}_KARMA_VIS={val!r}")
            if val is None:
                failures.append(
                    f"{name}: `{KARMA_VIS_ATTR}` is authored but has no value"
                )
            else:
                val_str = str(val).strip()
                # Karma syntax (see Houdini `BRAY_HdKarma_Geometry.ds`):
                # `*` = visible to all (does NOT exclude shadow);
                # `-shadow` = invisible to shadow (camera + secondary visible);
                # `*&-shadow` = same;
                # `primary` = visible only to camera rays (also excludes shadow).
                # The artist's Light-Path-IsCameraRay cutout maps to "exclude
                # shadow but stay otherwise visible", and Karma must actually
                # parse the value -- comma-delimited token lists with the
                # `camera` alias from the original PR #34 silently dropped the
                # surface entirely as soon as the primvar reached the geom prim.
                excludes_shadow = (
                    val_str == "-shadow"
                    or val_str == "primary"
                    or "-shadow" in val_str
                )
                wildcard_grants_all = (val_str == "*")
                # If the value uses comma-separated tokens with no Karma
                # syntactical markers (no `*`, no `-`, no `|`), it's the old
                # malformed list that Karma cannot parse.
                karma_syntax_ok = (
                    "-" in val_str
                    or val_str.startswith("*")
                    or "|" in val_str
                    or val_str == "primary"
                )
                if wildcard_grants_all or not excludes_shadow or not karma_syntax_ok:
                    failures.append(
                        f"{name}: `{KARMA_VIS_ATTR}` = {val_str!r} does not "
                        "encode a Karma-valid Light-Path no-shadow cutout. "
                        "Expected `-shadow` (or `*&-shadow`, `primary`) -- "
                        "Karma uses `|` separators and `-token` for "
                        "exclusions, not comma lists."
                    )
    return failures


def main():
    usd_path = export_critter_preview()
    print("MIRIS_EXPORTED_USD:", usd_path)

    stage = Usd.Stage.Open(usd_path)
    assert stage, "Failed to open exported USD stage"

    # Primary invariant: the materials whose opacity is driven by a Light-Path /
    # Transparent-BSDF cutout (body_purple and body_mouth_bag in critter-v001 --
    # the tongue interior + body's mouth-bag) get BOTH opacity=1.0 AND the
    # no-shadow Karma primvar.
    failures = []
    failures.extend(check_material(stage, "body_purple", require_rendervisibility=True))
    failures.extend(check_material(stage, "body_mouth_bag", require_rendervisibility=True))

    # Adjacent / PR #27 invariant: critter_tongue itself does NOT carry a Light-Path
    # cutout (its opacity socket is unlinked, the default-constant fallback resolves
    # it directly to opacity=1.0). It must still report opacity=1.0; no primvar is
    # expected.
    failures.extend(check_material(stage, "critter_tongue", require_rendervisibility=False))

    assert not failures, (
        "BL-MAT-OPACITY-LIGHTPATH-DROP NOT fixed:\n  " + "\n  ".join(failures)
    )

    print(
        "MIRIS_TEST_PASS: body_purple + body_mouth_bag carry opacity=1.0 AND "
        f"`{KARMA_VIS_ATTR}` excludes shadow rays; critter_tongue (no cutout) "
        "keeps opacity=1.0 unchanged. The Light-Path cutout's camera-vs-shadow "
        "intent is preserved without breaking the PR #27 creature_body invariant."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
