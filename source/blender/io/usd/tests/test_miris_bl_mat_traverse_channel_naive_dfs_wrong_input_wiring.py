"""Regression test for BL-MAT-traverse-channel-naive-dfs-wrong-input-wiring.

Repro: a Principled BSDF whose channels feed through color-modifying
intermediates (Hue/Saturation/Value, RGB Curves, Mix, Color Ramp, Map Range)
with an Image Texture wired into a CONTROL input (Mix Factor) downstream.
Stock Blender's `traverse_channel` does a naive DFS through every input of
every intermediate node it walks across, including modulator wires. The
result: a `BSDF.Base Color` upstream walk slides through a Mix's `Factor`
input, into a `Map Range`, and grabs the Image Texture feeding the
roughness/control channel — then attaches it as the source of
`diffuseColor` on the exported UsdPreviewSurface. This silently mis-attributes
the wrong texture and silently flattens the artist's color-modification
intent.

Concrete swarmfish symptom captured in the diagnostic dumps:
  - diffuseColor.connected_to = Image_Texture_002.rgb  (roughness texture)
  - emissiveColor.connected_to = Image_Texture_002.rgb (same wrong texture)
  - normal.connected_to = Image_Texture.rgb            (diffuse texture)

After the fix, `traverse_channel` only descends through nodes whose
signal-flow we can identify (Reroute, Normal Map, Bump, Separate/Combine
Color, Mapping, Math, Vector Math). At color-modifying intermediates (RGB
Curves, Hue/Saturation, Mix, Color Ramp, Map Range, Float Curve,
Bright/Contrast, Gamma, Invert, Clamp) it halts — the UsdPreviewSurface
input is left to use the BSDF socket's authored constant rather than a
misattributed texture.

This regression test builds a synthetic minimal repro of the swarmfish
pattern (HSV on Base Color, Mix on Roughness, Map-Range driving the Factor
from a different Image Texture). It does NOT depend on the AYON corpus.
Running against the same .blend via the patched binary the production
swarmfish chain is also re-validated; this synthetic test is the
unit-style sentinel.

Run with the patched Blender:

  /Users/d.smith/MirisProjects/build_darwin/bin/Blender.app/Contents/MacOS/Blender \\
      --background --python \\
      source/blender/io/usd/tests/test_miris_bl_mat_traverse_channel_naive_dfs_wrong_input_wiring.py
"""

import os
import sys
import tempfile
import traceback

import bpy

# Texture image filenames (sentinel base names we look for in the export).
DIFFUSE_FILE = "diffuse_red.png"
ROUGHNESS_FILE = "roughness_gray.png"
CONTROL_FILE = "control_mask.png"


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def make_dummy_image(tmpdir, name, color):
    """Create a tiny 4x4 image on disk and return the loaded bpy.types.Image."""
    path = os.path.join(tmpdir, name)
    img = bpy.data.images.new(name, width=4, height=4, alpha=False)
    img.filepath_raw = path
    img.file_format = "PNG"
    # Fill with a flat color.
    pixels = list(color) * (4 * 4)
    img.pixels = pixels
    img.save()
    return img


def build_repro_scene(tmpdir):
    bpy.ops.wm.read_factory_settings(use_empty=True)

    mesh = bpy.data.meshes.new("ReproMesh")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new("ReproObject", mesh)
    bpy.context.collection.objects.link(obj)

    img_diffuse = make_dummy_image(tmpdir, DIFFUSE_FILE, [0.9, 0.1, 0.1, 1.0])
    img_roughness = make_dummy_image(tmpdir, ROUGHNESS_FILE, [0.5, 0.5, 0.5, 1.0])
    img_control = make_dummy_image(tmpdir, CONTROL_FILE, [0.2, 0.2, 0.2, 1.0])

    mat = bpy.data.materials.new("ReproMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    output = nt.nodes.new("ShaderNodeOutputMaterial")
    output.location = (1000, 0)

    principled = nt.nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (700, 0)

    # --- Base Color chain: Image_diffuse -> HSV -> Principled.Base Color
    # The HSV is a color-modifying intermediate that the old naive DFS would
    # walk through, and ALSO walk through its non-Color inputs, picking up the
    # wrong upstream texture from a control wire below.
    tex_diffuse = nt.nodes.new("ShaderNodeTexImage")
    tex_diffuse.name = "TexDiffuse"
    tex_diffuse.image = img_diffuse
    tex_diffuse.location = (-200, 200)

    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    hsv.location = (200, 200)
    nt.links.new(tex_diffuse.outputs["Color"], hsv.inputs["Color"])
    # CRUCIAL: also wire something INTO the HSV's Saturation input from a
    # totally unrelated image texture. Naive DFS will iterate every HSV input
    # before reaching Color (Hue, Saturation, Value, Fac, Color in order) and
    # find this control-wire image first.
    tex_control = nt.nodes.new("ShaderNodeTexImage")
    tex_control.name = "TexControl"
    tex_control.image = img_control
    tex_control.location = (-200, -200)
    sep_color = nt.nodes.new("ShaderNodeSeparateColor")
    sep_color.location = (0, -200)
    nt.links.new(tex_control.outputs["Color"], sep_color.inputs["Color"])
    # Feed the control's Red channel into HSV.Saturation (a non-color input
    # we MUST not walk through when looking upstream from Base Color).
    nt.links.new(sep_color.outputs["Red"], hsv.inputs["Saturation"])

    nt.links.new(hsv.outputs["Color"], principled.inputs["Base Color"])

    # --- Roughness chain: Image_roughness -> Mix -> Principled.Roughness
    # Mix is a color-modifying intermediate; we should NOT pass through it.
    # We also wire a Map Range into the Factor, where Map Range reads from
    # tex_control — naive DFS would find that control image and attach it as
    # the roughness source.
    tex_roughness = nt.nodes.new("ShaderNodeTexImage")
    tex_roughness.name = "TexRoughness"
    tex_roughness.image = img_roughness
    tex_roughness.location = (200, 500)

    map_range = nt.nodes.new("ShaderNodeMapRange")
    map_range.location = (200, 700)
    nt.links.new(sep_color.outputs["Green"], map_range.inputs["Value"])

    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "FLOAT"
    mix.location = (500, 500)
    nt.links.new(tex_roughness.outputs["Color"], mix.inputs[2])  # A_Float
    nt.links.new(map_range.outputs["Result"], mix.inputs[0])  # Factor

    nt.links.new(mix.outputs["Result"], principled.inputs["Roughness"])

    # --- Metallic: constant (no chain) -- sanity reference.
    principled.inputs["Metallic"].default_value = 0.0

    nt.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    obj.data.materials.append(mat)
    return obj, mat


def main():
    tmp = tempfile.mkdtemp(prefix="bl_mat_traverse_channel_")
    obj, mat = build_repro_scene(tmp)
    out = os.path.join(tmp, "traverse_channel_repro.usda")

    try:
        bpy.ops.wm.usd_export(
            filepath=out,
            check_existing=False,
            export_materials=True,
            export_meshes=True,
            generate_preview_surface=True,
            generate_materialx_network=False,
            evaluation_mode="RENDER",
            root_prim_path="/root",
        )
    except Exception as e:
        traceback.print_exc()
        fail(f"usd_export raised: {e}")

    print(f"USD_EXPORTED: {out}")

    try:
        from pxr import Usd, UsdShade, Sdf
    except ImportError as e:
        fail(f"pxr not importable from this Blender build: {e}")

    stage = Usd.Stage.Open(out)
    if not stage:
        fail("Failed to open exported stage")

    # Find the UsdPreviewSurface shader.
    surface_shader = None
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Shader):
            shader = UsdShade.Shader(prim)
            shader_id = shader.GetIdAttr().Get() if shader.GetIdAttr() else ""
            if shader_id == "UsdPreviewSurface":
                surface_shader = shader
                break
    if surface_shader is None:
        fail("No UsdPreviewSurface shader found in exported stage")

    # --- Assertion 1: diffuseColor must NOT be connected to TexControl.
    # In the buggy export, the naive DFS walks Base Color -> HSV -> (iterates
    # all inputs in order: Hue, Saturation, Value, Fac, Color) and finds
    # TexControl via the Saturation->SeparateColor->TexControl chain BEFORE
    # reaching the legitimate Color->TexDiffuse path. The fix halts at HSV.
    diffuse_input = surface_shader.GetInput("diffuseColor")
    if diffuse_input:
        connected_src = diffuse_input.GetConnectedSource()
        if connected_src:
            src_shader_api, src_name, _ = connected_src
            src_path = src_shader_api.GetPath().pathString
            print(f"diffuseColor connected to: {src_path} (source name: {src_name})")
            # Inspect what file that texture references.
            src_prim = stage.GetPrimAtPath(src_path)
            src_shader = UsdShade.Shader(src_prim)
            file_input = src_shader.GetInput("file")
            if file_input:
                asset = file_input.Get()
                if asset and CONTROL_FILE in str(asset.path):
                    fail(
                        f"BL-MAT-traverse-channel-naive-dfs-wrong-input-wiring: "
                        f"diffuseColor connected to texture sourcing the CONTROL "
                        f"image ({asset.path}). The exporter walked through HSV's "
                        f"Saturation input (a non-color modulator) and latched "
                        f"onto the wrong texture from an unrelated control wire."
                    )
                if asset and ROUGHNESS_FILE in str(asset.path):
                    fail(
                        f"BL-MAT-traverse-channel-naive-dfs-wrong-input-wiring: "
                        f"diffuseColor connected to the ROUGHNESS image "
                        f"({asset.path}). Same misattribution class — cross-channel "
                        f"texture leakage."
                    )
                # If it connects to the legitimate diffuse texture, that's
                # *also* acceptable — that would mean the walker safely passed
                # through HSV.Color only. We accept it as a possible behavior.

    # --- Assertion 2: roughness must NOT be connected to TexControl.
    rough_input = surface_shader.GetInput("roughness")
    if rough_input:
        connected_src = rough_input.GetConnectedSource()
        if connected_src:
            src_shader_api, src_name, _ = connected_src
            src_path = src_shader_api.GetPath().pathString
            print(f"roughness connected to: {src_path} (source name: {src_name})")
            src_prim = stage.GetPrimAtPath(src_path)
            src_shader = UsdShade.Shader(src_prim)
            file_input = src_shader.GetInput("file")
            if file_input:
                asset = file_input.Get()
                if asset and CONTROL_FILE in str(asset.path):
                    fail(
                        f"BL-MAT-traverse-channel-naive-dfs-wrong-input-wiring: "
                        f"roughness connected to texture sourcing the CONTROL "
                        f"image ({asset.path}). Naive DFS walked through the Mix "
                        f"Factor -> Map Range -> SeparateColor -> TexControl chain."
                    )

    # --- Assertion 3: emissiveColor (unwritten in the .blend) must not get
    # the wrong texture either. The current exporter writes emissive_color
    # from the same Base Color path under some circumstances, so re-check it.
    emit_input = surface_shader.GetInput("emissiveColor")
    if emit_input:
        connected_src = emit_input.GetConnectedSource()
        if connected_src:
            src_shader_api, _, _ = connected_src
            src_path = src_shader_api.GetPath().pathString
            src_prim = stage.GetPrimAtPath(src_path)
            src_shader = UsdShade.Shader(src_prim)
            file_input = src_shader.GetInput("file")
            if file_input:
                asset = file_input.Get()
                if asset and CONTROL_FILE in str(asset.path):
                    fail(
                        f"BL-MAT-traverse-channel-naive-dfs-wrong-input-wiring: "
                        f"emissiveColor wrongly connected to CONTROL image "
                        f"({asset.path})."
                    )

    print(
        "PASS: BL-MAT-traverse-channel-naive-dfs-wrong-input-wiring — "
        "exporter no longer walks through modulator inputs of color-modifying "
        "intermediates and misattributes textures across channels."
    )


if __name__ == "__main__":
    main()
