"""Regression test for BL-LIT-005-domelight-texture-invisible-as-background.

When a Blender scene's World uses an Environment Texture node feeding a
Background shader with a non-default Strength, the stock USD exporter:

  1. Authors `inputs:texture:file` but leaves `inputs:texture:format` at the
     schema default `automatic`. Karma XPU documents that it only supports
     lat-long environment maps on dome lights, so the un-authored format
     can leave the dome textureless for some renderers.

  2. Drops the Background node's `Strength` whenever an image is connected
     (`world_material_to_dome_light` only authored intensity in the
     color-only branch). A scene with `Background.Strength = 5.0` exports
     as a dome at the schema default `inputs:intensity = 1.0`, which both
     darkens IBL contribution 5x and (in delegates that gate camera
     visibility on a non-zero intensity threshold) suppresses the dome as
     a backdrop entirely.

  3. Authors no Karma-specific camera-visibility primvar, so any Karma
     pipeline that consults `primvars:karma:object:rendervisibility` for
     per-light camera visibility falls through to its delegate default.

The fork patch makes `world_material_to_dome_light`:

  * always author `inputs:intensity` from the Background node's Strength,
  * author `inputs:texture:format = "latlong"` whenever a texture file is
    present, and
  * author `primvars:karma:object:rendervisibility = "*"` so Karma
    explicitly sees the dome on primary camera rays.

This test loads a real AYON `.blend` (mikassa-v001.blend), rewires the
world to a `brown_photostudio_06_4k.exr` Environment Texture at a
non-default Strength, exports, then asserts the three attributes are
authored with the expected values.

Run via:
  /path/to/patched/Blender --background \\
    <mikassa-v001.blend> --python <this-file> -- \\
    --output-usd /tmp/mikassa_dome.usda \\
    --hdri /Users/d.smith/MirisProjects/AYON/brown_photostudio_06_4k.exr
"""

import argparse
import os
import sys

import bpy

EXPECTED_STRENGTH = 5.0


def parse_args() -> argparse.Namespace:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1 :]
    else:
        args = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-usd", required=True)
    parser.add_argument("--hdri", required=True)
    return parser.parse_args(args)


def wire_world_hdri(world: bpy.types.World, hdri_path: str, strength: float) -> None:
    """Replace the world's Background.Color with an Environment Texture and
    set Background.Strength to a non-default value."""
    world.use_nodes = True
    nt = world.node_tree
    # Drop any pre-existing image / mapping / coord nodes so we start clean.
    to_remove = [n for n in nt.nodes if n.type in {"TEX_ENVIRONMENT", "MAPPING", "TEX_COORD"}]
    for n in to_remove:
        nt.nodes.remove(n)

    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
    assert bg is not None, "World is missing the Background node we rely on."

    tex = nt.nodes.new(type="ShaderNodeTexEnvironment")
    img = bpy.data.images.load(hdri_path, check_existing=True)
    tex.image = img
    nt.links.new(tex.outputs["Color"], bg.inputs["Color"])

    bg.inputs["Strength"].default_value = strength


def make_everything_visible() -> None:
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        try:
            obj.hide_set(False)
        except Exception:
            pass
    for coll in bpy.data.collections:
        coll.hide_viewport = False
        coll.hide_render = False
    bpy.context.view_layer.update()


def main() -> None:
    cli = parse_args()
    out_usd = os.path.abspath(cli.output_usd)
    os.makedirs(os.path.dirname(out_usd), exist_ok=True)

    assert os.path.isfile(cli.hdri), f"HDRI not found at {cli.hdri!r}"
    wire_world_hdri(bpy.context.scene.world, cli.hdri, EXPECTED_STRENGTH)
    make_everything_visible()

    print(f"[bl-lit-005] exporting USD -> {out_usd}")
    result = bpy.ops.wm.usd_export(
        filepath=out_usd,
        export_textures_mode="KEEP",
        generate_preview_surface=True,
        export_materials=True,
        export_meshes=True,
        export_lights=True,
        export_cameras=True,
        convert_world_material=True,
        evaluation_mode="RENDER",
        selected_objects_only=False,
    )
    assert "FINISHED" in result, f"usd_export did not finish cleanly: {result}"
    print(f"[bl-lit-005] EXPORTED_USD_PATH={out_usd}")

    from pxr import Usd, UsdLux, Tf

    stage = Usd.Stage.Open(out_usd)
    assert stage is not None, f"Failed to open exported stage at {out_usd}"

    # Find the env_light dome. The writer puts it at "<root_prim>/env_light".
    dome_prims = [
        prim for prim in stage.Traverse()
        if prim.GetTypeName() == "DomeLight" and prim.GetName() == "env_light"
    ]
    assert len(dome_prims) == 1, (
        f"Regression: expected exactly one /<root>/env_light DomeLight prim, "
        f"got {len(dome_prims)}: {[p.GetPath() for p in dome_prims]}"
    )
    dome_prim = dome_prims[0]
    dome = UsdLux.DomeLight(dome_prim)
    print(f"[bl-lit-005] dome prim: {dome_prim.GetPath()}")

    # ASSERT 1: inputs:intensity reflects the Background node's Strength,
    # not the schema default of 1.0.
    intensity = dome.GetIntensityAttr().Get()
    assert intensity == EXPECTED_STRENGTH, (
        f"Regression (BL-LIT-005): inputs:intensity = {intensity!r}, expected "
        f"{EXPECTED_STRENGTH!r} (the world Background's Strength). The image "
        f"branch of world_material_to_dome_light has dropped the Strength again."
    )
    assert dome.GetIntensityAttr().HasAuthoredValue(), (
        "Regression (BL-LIT-005): inputs:intensity is unauthored; the writer "
        "must explicitly set it from Background.Strength."
    )

    # ASSERT 2: inputs:texture:format is authored as "latlong", not left at
    # the schema default "automatic".
    fmt_attr = dome.GetTextureFormatAttr()
    assert fmt_attr.HasAuthoredValue(), (
        "Regression (BL-LIT-005): inputs:texture:format was not authored. "
        "Blender's Environment Texture is always equirectangular; the "
        "exporter must declare latlong explicitly."
    )
    fmt = fmt_attr.Get()
    assert str(fmt) == "latlong", (
        f"Regression (BL-LIT-005): inputs:texture:format = {fmt!r}, expected "
        f"'latlong'."
    )

    # ASSERT 3: primvars:karma:object:rendervisibility is authored as "*"
    # so the dome appears as the camera backdrop in Karma.
    karma_vis = dome_prim.GetAttribute("primvars:karma:object:rendervisibility")
    assert karma_vis.IsValid() and karma_vis.HasAuthoredValue(), (
        "Regression (BL-LIT-005): primvars:karma:object:rendervisibility was "
        "not authored on the dome. Karma needs this to render the dome's "
        "texture on primary camera rays."
    )
    vis_value = karma_vis.Get()
    assert vis_value == "*", (
        f"Regression (BL-LIT-005): primvars:karma:object:rendervisibility = "
        f"{vis_value!r}, expected '*'."
    )

    # Sanity: the texture file path is still authored.
    tex_attr = dome.GetTextureFileAttr()
    assert tex_attr.HasAuthoredValue(), (
        "Regression: inputs:texture:file vanished — broader writer regression."
    )

    print("[bl-lit-005] PASS")


if __name__ == "__main__":
    main()
