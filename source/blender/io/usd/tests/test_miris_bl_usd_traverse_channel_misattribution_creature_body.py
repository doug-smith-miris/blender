"""Real-asset regression test for `bl-usd-traverse-channel-misattribution:creature-body`.

The swarmfish `creature-body` material is a painterly composite: its Principled
BSDF `Base Color` / `Emission Color` are fed through a chain of color-modifying
nodes (Hue/Saturation/Value -> Mix -> Map Range / RGB Curves), and the roughness
texture (`roughness-watercolor.exr`) is wired into a *control* input (a Mix
`Factor` driven by a Map Range) deep inside that chain.

Stock Blender's `traverse_channel` walked a naive depth-first search through
*every* input of every intermediate node -- including modulator/control wires.
Walking `Base Color` upstream it slid through the Hue/Saturation/Mix control
inputs, reached the first Image Texture it could find (the roughness EXR), and
attached THAT as the source of `diffuseColor`. The diagnostic dumps for
swarmfish-v001 captured the exact cross-channel leakage:

    diffuseColor  -> Image_Texture_002.rgb   (roughness-watercolor.exr)   WRONG
    emissiveColor -> Image_Texture_002.rgb   (roughness-watercolor.exr)   WRONG
    normal        -> Image_Texture.rgb       (watercolor_blended_02.png)  WRONG
    roughness     -> Image_Texture_002.r     (reached via a Mix Factor)   WRONG

The fix in usd_writer_material.cc restricts `traverse_channel` to a per-node-type
whitelist of signal-carrying inputs (`is_signal_carrying_input`) and halts at
color-modifying intermediates (Hue/Saturation, RGB Curves, Mix, Color Ramp, Map
Range, ...). After the fix the cross-channel leakage is gone: `creature_body`'s
color channels are no longer wired to the roughness EXR. (The artist's true
albedo `background_creatures_diffuse_<UDIM>.tif` lives behind the same
color-modifying nodes -- and is used as a vector-math operand, not a direct
color source -- so it is NOT representable as a single UsdPreviewSurface
texture; the allowlist correctly falls back to the BSDF socket's authored
constant rather than a misattributed texture. Faithful albedo recovery would
require a bake-to-texture pass, tracked as a follow-on mission.)

Regression sentinel:
  - STOCK   /Applications/Blender.app : diffuseColor/emissiveColor CONNECTED to
            a UsdUVTexture whose file is roughness-watercolor.exr -> this test FAILS.
  - PATCHED build_darwin binary       : diffuseColor/emissiveColor are NOT
            connected to the roughness EXR (authored constants) -> this test PASSES.

Run headless with the PATCHED build:
  build_darwin/.../Blender --background <swarmfish-v001.blend> \
      --python test_miris_bl_usd_traverse_channel_misattribution_creature_body.py
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

# The roughness map that the naive DFS misattributed onto the color channels.
ROUGHNESS_FILE_BASENAME = "roughness-watercolor.exr"
# The diffuse-side texture the naive DFS misattributed onto `normal`.
DIFFUSE_PNG_BASENAME = "watercolor_blended_02.png"

# Color/normal channels that, post-fix, must NOT carry the roughness EXR.
COLOR_CHANNELS = ("diffuseColor", "emissiveColor")


def export_swarmfish_preview():
    out_dir = tempfile.mkdtemp(prefix="miris_traverse_misattr_")
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


def connected_texture_file(stage, shader, input_name):
    """If `shader.input_name` is connected to a UsdUVTexture, return the basename
    of that texture's `file` asset. Otherwise return None."""
    inp = shader.GetInput(input_name)
    if not inp:
        return None
    sources = inp.GetConnectedSources()
    if not sources or not sources[0]:
        return None
    src = sources[0][0]
    src_prim = stage.GetPrimAtPath(src.source.GetPath())
    src_shader = UsdShade.Shader(src_prim)
    if not src_shader or src_shader.GetIdAttr().Get() != "UsdUVTexture":
        return None
    file_inp = src_shader.GetInput("file")
    if not file_inp:
        return None
    asset = file_inp.Get()
    if asset is None:
        return None
    return os.path.basename(str(asset.path))


def main():
    usd_path = export_swarmfish_preview()
    print("MIRIS_EXPORTED_USD:", usd_path)

    stage = Usd.Stage.Open(usd_path)
    assert stage, "Failed to open exported USD stage"

    material = find_material(stage, "creature_body")
    assert material is not None, "creature_body Material prim missing entirely"

    surface = find_preview_surface(material)
    assert surface is not None, (
        "creature_body has no UsdPreviewSurface shader -- cannot validate wiring"
    )

    failures = []

    # 1) No color channel may be wired to the roughness EXR (the core misattribution).
    for chan in COLOR_CHANNELS:
        tex = connected_texture_file(stage, surface, chan)
        print(f"MIRIS_CHANNEL {chan} -> texture={tex}")
        if tex == ROUGHNESS_FILE_BASENAME:
            failures.append(
                f"{chan} is connected to {ROUGHNESS_FILE_BASENAME} "
                f"(cross-channel misattribution via control wire NOT eliminated)"
            )

    # 2) `normal` must not be wired to the diffuse-side color PNG.
    normal_tex = connected_texture_file(stage, surface, "normal")
    print(f"MIRIS_CHANNEL normal -> texture={normal_tex}")
    if normal_tex == DIFFUSE_PNG_BASENAME:
        failures.append(
            f"normal is connected to {DIFFUSE_PNG_BASENAME} "
            f"(naive DFS still latches a non-normal texture onto the normal channel)"
        )

    # 3) Belt-and-suspenders: the roughness EXR must not appear on ANY of the
    #    surface's color/normal inputs.
    for chan in COLOR_CHANNELS + ("normal",):
        tex = connected_texture_file(stage, surface, chan)
        if tex == ROUGHNESS_FILE_BASENAME:
            failures.append(f"{chan} still resolves to the roughness EXR")

    assert not failures, (
        "creature_body traverse_channel misattribution NOT fixed:\n  "
        + "\n  ".join(failures)
    )

    print(
        "MIRIS_TEST_PASS: creature_body color/normal channels are no longer "
        "misattributed to roughness-watercolor.exr -- the naive-DFS cross-channel "
        "texture leakage is eliminated."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
