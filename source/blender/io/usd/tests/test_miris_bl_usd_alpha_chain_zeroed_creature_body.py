"""Real-asset regression test for `bl-usd-alpha-chain-zeroed:creature-body`.

The swarmfish `creature-body` material drives its active Principled BSDF `Alpha`
through a render-visibility *cutout*: the Alpha socket is linked to a chain that
resolves to `Mix(fac = Light Path > Is Camera Ray)` (and the loose second
Principled BSDF in the tree is fully opaque, Alpha = 1.0). The cutout keeps the
body fully visible to the camera while suppressing it for secondary rays -- it
is NOT a literal per-pixel surface transparency.

UsdPreviewSurface has no way to express a Light-Path cutout, so the writer falls
through to its default-constant path for the linked Alpha socket. Stock Blender
emitted that socket's *stale* `default_value` (0.0 for creature_body) -- and the
fork additionally inverted it -- collapsing the channel to:

    float inputs:opacity = 0          (creature_body fully INVISIBLE in Karma)

The fix in usd_writer_material.cc treats a *linked* opacity socket whose chain
cannot be reduced to a representable texture/attribute source as fully opaque
(`opacity = 1.0`) instead of reading the stale socket default, and detects the
Light Path / Transparent BSDF cutout topology explicitly
(`opacity_socket_drives_visibility_cutout`). The unlinked-constant path is also
corrected so that Alpha maps directly to opacity (alpha=1 -> opaque) while only
Transmission Weight is inverted.

Regression sentinel:
  - STOCK   /Applications/Blender.app : creature_body opacity == 0.0 -> FAILS.
  - PATCHED build_darwin binary       : creature_body opacity == 1.0 -> PASSES.

Run headless with the PATCHED build:
  build_darwin/.../Blender --background <swarmfish-v001.blend> \
      --python test_miris_bl_usd_alpha_chain_zeroed_creature_body.py
"""

import os
import sys
import tempfile

import bpy
from pxr import Usd, UsdShade

SWARMFISH_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/swarmfish/publish/swarmfish-v001.blend"
)


def export_swarmfish_preview():
    out_dir = tempfile.mkdtemp(prefix="miris_alpha_chain_zeroed_")
    usd_path = os.path.join(out_dir, "swarmfish-preview.usda")

    # Unhide everything so geometry-nodes meshes (and their materials) survive.
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
    """Return the UsdPreviewSurface shader prim under the material, or None."""
    for child in material.GetPrim().GetChildren():
        shader = UsdShade.Shader(child)
        if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
            return shader
    return None


def main():
    usd_path = export_swarmfish_preview()
    print("MIRIS_EXPORTED_USD:", usd_path)

    stage = Usd.Stage.Open(usd_path)
    assert stage, "Failed to open exported USD stage"

    material = find_material(stage, "creature_body")
    assert material is not None, "creature_body Material prim missing entirely"

    surface = find_preview_surface(material)
    assert surface is not None, (
        "creature_body has no UsdPreviewSurface shader -- cannot validate opacity"
    )

    opacity_input = surface.GetInput("opacity")
    assert opacity_input is not None, "creature_body UsdPreviewSurface has no opacity input"

    # The cutout chain must NOT be reduced to a connection; it has no representable
    # texture source. The writer authors a constant fully-opaque value instead.
    connected = bool(opacity_input.GetConnectedSources()[0])
    opacity_val = opacity_input.Get()
    print(f"MIRIS_OPACITY connected={connected} value={opacity_val}")

    failures = []

    if connected:
        failures.append(
            "opacity is connected to a shader source -- a Light-Path/Transparent "
            "cutout is not representable as a UsdPreviewSurface opacity texture"
        )

    if opacity_val is None:
        failures.append("opacity has no authored constant value")
    elif abs(float(opacity_val)) < 1e-6:
        failures.append(
            "opacity == 0.0 -- the Light-Path Alpha cutout was collapsed to fully "
            "transparent (creature_body renders INVISIBLE in Karma). The stale "
            "linked-socket default was emitted instead of resolving the cutout to "
            "opaque."
        )
    elif abs(float(opacity_val) - 1.0) > 1e-6:
        failures.append(
            f"opacity == {float(opacity_val)} -- expected 1.0 (fully opaque) for an "
            f"unrepresentable Light-Path visibility cutout"
        )

    assert not failures, (
        "creature_body alpha-chain-zeroed NOT fixed:\n  " + "\n  ".join(failures)
    )

    print(
        "MIRIS_TEST_PASS: creature_body opacity resolves to 1.0 (opaque) instead of "
        "the collapsed 0.0 -- the Light-Path Alpha cutout no longer renders the body "
        "invisible."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
