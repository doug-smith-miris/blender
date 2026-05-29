"""Regression test for BL-LIT-002:world-domelight-brightness-divergence-karma-vs-cycles.

Blender's World `Background` Strength is a *dimensionless radiance multiplier* on the
environment color/texture; UsdLuxDomeLight `inputs:intensity` is likewise a linear radiance
multiplier on `inputs:texture:file`. The physically-consistent conversion is therefore unity
(Blender's own importer round-trips it as unity). The stock exporter, however, only authored
`inputs:intensity` in the solid-color branch — when an Environment Texture was connected the
Strength was dropped and the dome defaulted to intensity = 1.0, lighting the scene
`Strength`x too dimly versus the Cycles reference. It also left `inputs:texture:format` at the
schema default `automatic` (Karma documents lat-long-only dome maps) and authored no Karma
camera-visibility primvar, both contributors to the Cycles-vs-Karma brightness divergence.

The fork patch makes `world_material_to_dome_light`:

  * always author `inputs:intensity` = Background.Strength * WORLD_DOME_LIGHT_INTENSITY_SCALE
    (unity scale: world Strength is already a radiance multiplier, not a Watt power),
  * author `inputs:texture:format = "latlong"` whenever a texture is present, and
  * author `primvars:karma:object:rendervisibility = "*"`.

This test loads the real AYON mikassa-v001.blend, rewires the world to a
brown_photostudio_06_4k.exr Environment Texture at a non-default Strength, exports, then
asserts the dome carries the faithful intensity (== Strength) plus the latlong/visibility
attributes. The intensity == Strength assertion is the BL-LIT-002 regression sentinel: it
catches both a dropped Strength (intensity → 1.0) and any spurious unit rescale.

Run via:
  /path/to/patched/Blender --background <mikassa-v001.blend> --python <this-file> -- \\
    --output-usd /tmp/bllit002/mikassa_dome.usda \\
    --hdri /Users/d.smith/MirisProjects/AYON/brown_photostudio_06_4k.exr
"""

import argparse
import os
import sys

import bpy

EXPECTED_STRENGTH = 5.0
# The fork's documented Blender-Strength -> USD-intensity scale (unity for the world dome).
EXPECTED_INTENSITY_SCALE = 1.0


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-usd", required=True)
    parser.add_argument("--hdri", required=True)
    return parser.parse_args(argv)


def wire_world_hdri(world: bpy.types.World, hdri_path: str, strength: float) -> None:
    """Replace the world's Background.Color input with an Environment Texture and set a
    non-default Background.Strength."""
    world.use_nodes = True
    nt = world.node_tree
    for n in [n for n in nt.nodes if n.type in {"TEX_ENVIRONMENT", "MAPPING", "TEX_COORD"}]:
        nt.nodes.remove(n)

    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
    assert bg is not None, "World is missing the Background node we rely on."

    tex = nt.nodes.new(type="ShaderNodeTexEnvironment")
    tex.image = bpy.data.images.load(hdri_path, check_existing=True)
    nt.links.new(tex.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = strength


def main() -> None:
    cli = parse_args()
    out_usd = os.path.abspath(cli.output_usd)
    os.makedirs(os.path.dirname(out_usd), exist_ok=True)
    assert os.path.isfile(cli.hdri), f"HDRI not found at {cli.hdri!r}"

    wire_world_hdri(bpy.context.scene.world, cli.hdri, EXPECTED_STRENGTH)

    print(f"[bl-lit-002] exporting USD -> {out_usd}")
    result = bpy.ops.wm.usd_export(
        filepath=out_usd,
        export_textures_mode="KEEP",
        generate_preview_surface=True,
        export_materials=True,
        export_lights=True,
        export_cameras=True,
        convert_world_material=True,
        evaluation_mode="RENDER",
        selected_objects_only=False,
    )
    assert "FINISHED" in result, f"usd_export did not finish cleanly: {result}"
    print(f"[bl-lit-002] EXPORTED_USD_PATH={out_usd}")

    from pxr import Usd, UsdLux

    stage = Usd.Stage.Open(out_usd)
    assert stage is not None, f"Failed to open exported stage at {out_usd}"

    dome_prims = [
        p for p in stage.Traverse()
        if p.GetTypeName() == "DomeLight" and p.GetName() == "env_light"
    ]
    assert len(dome_prims) == 1, (
        f"Expected exactly one /<root>/env_light DomeLight, got {len(dome_prims)}: "
        f"{[p.GetPath() for p in dome_prims]}"
    )
    dome_prim = dome_prims[0]
    dome = UsdLux.DomeLight(dome_prim)
    print(f"[bl-lit-002] dome prim: {dome_prim.GetPath()}")

    # ASSERT 1 (BL-LIT-002 sentinel): intensity faithfully reflects Background.Strength,
    # neither dropped to 1.0 nor spuriously unit-rescaled.
    expected = EXPECTED_STRENGTH * EXPECTED_INTENSITY_SCALE
    intensity = dome.GetIntensityAttr().Get()
    assert dome.GetIntensityAttr().HasAuthoredValue(), (
        "Regression (BL-LIT-002): inputs:intensity is unauthored; the dome defaults to 1.0 and "
        "renders Strength-x too dim versus Cycles."
    )
    assert abs(float(intensity) - expected) < 1e-4, (
        f"Regression (BL-LIT-002): inputs:intensity = {intensity!r}, expected {expected!r} "
        f"(Background.Strength {EXPECTED_STRENGTH} * scale {EXPECTED_INTENSITY_SCALE}). A value "
        f"of 1.0 means the image branch dropped Strength again; any other value means the "
        f"Watts->physical scale drifted."
    )

    # ASSERT 2: equirect format authored explicitly (not schema default 'automatic').
    fmt_attr = dome.GetTextureFormatAttr()
    assert fmt_attr.HasAuthoredValue() and str(fmt_attr.Get()) == "latlong", (
        f"Regression (BL-LIT-002): inputs:texture:format = "
        f"{fmt_attr.Get()!r} (authored={fmt_attr.HasAuthoredValue()}), expected 'latlong'."
    )

    # ASSERT 3: Karma camera-visibility primvar authored so the dome is the visible backdrop.
    karma_vis = dome_prim.GetAttribute("primvars:karma:object:rendervisibility")
    assert karma_vis.IsValid() and karma_vis.HasAuthoredValue() and karma_vis.Get() == "*", (
        "Regression (BL-LIT-002): primvars:karma:object:rendervisibility not authored as '*'."
    )

    # Sanity: the HDRI path survives.
    assert dome.GetTextureFileAttr().HasAuthoredValue(), "inputs:texture:file vanished."

    print("[bl-lit-002] PASS")


if __name__ == "__main__":
    main()
