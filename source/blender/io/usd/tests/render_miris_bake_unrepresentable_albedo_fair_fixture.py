"""
Isolated fair-fixture verification for bake-unrepresentable-albedo.

Reproduces the *category* the bite targets:
   Principled BSDF Base Color reached through a color-modifying node
   (Hue/Saturation/Value) so the exporter's allowlist correctly refuses
   to wire any upstream Image Texture as the UsdPreviewSurface diffuse,
   leaving diffuseColor at its socket default. The bake driver should
   close the gap so Karma renders the hue-shifted color rather than a
   flat default.

Fixture: a single UV-Sphere with a single material whose Principled
BSDF Base Color is fed by:
    Image Texture (uniform blue, 64x64) -> Hue/Saturation (Hue=0.7,
    rotating blue ~->yellow-orange) -> Principled BSDF.Base Color
Even sky-dome environment, no local lights. Sphere fills the frame.

Outputs (all in OUT_DIR):
    bake_unrep_fixture.blend       -- the saved scene (for repro)
    bake_unrep_fixture_baked.usda  -- USD with bake-rewired diffuseColor
    render_cycles_reference.png    -- Cycles ground-truth render
"""
import math
import os
import sys

import bpy

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from miris_bake_unrepresentable_albedo import bake_and_export  # noqa: E402

OUT_DIR = "/tmp/bake_unrep_fixture"
RES = (768, 768)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def build_scene():
    reset_scene()
    scn = bpy.context.scene
    scn.render.resolution_x = RES[0]
    scn.render.resolution_y = RES[1]
    scn.render.resolution_percentage = 100
    scn.render.film_transparent = False

    # World: a flat mid-grey sky so the sphere reads cleanly.
    world = bpy.data.worlds.new("FixtureWorld")
    scn.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.55, 0.55, 0.55, 1.0)
    bg.inputs["Strength"].default_value = 1.0

    # Sphere (smooth-shaded, fills the frame).
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=64, ring_count=32,
                                         location=(0.0, 0.0, 0.0))
    sphere = bpy.context.active_object
    bpy.ops.object.shade_smooth()

    # Camera framing the sphere head-on.
    bpy.ops.object.camera_add(location=(0.0, -3.2, 0.0),
                              rotation=(math.radians(90.0), 0.0, 0.0))
    cam = bpy.context.active_object
    scn.camera = cam
    cam.data.lens = 50.0

    # The fair-fixture material: blue Image Texture -> HSV(hue=0.7) -> BSDF.Base Color.
    # The Hue shift rotates the blue (~240deg) by 0.7*360 = 252deg into
    # the yellow/orange band, so a successful render is yellow-orange.
    mat = bpy.data.materials.new("FixtureMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    # Make it lambertian-ish so the rendered color reads the diffuse.
    bsdf.inputs["Specular IOR Level"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Metallic"].default_value = 0.0

    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    hsv.location = (40, 0)
    hsv.inputs["Hue"].default_value = 0.7
    hsv.inputs["Saturation"].default_value = 1.0
    hsv.inputs["Value"].default_value = 1.0
    hsv.inputs["Fac"].default_value = 1.0

    # The "upstream texture" is a tiny uniform-blue image (so the
    # allowlist refuses to wire it through HSV).
    img = bpy.data.images.new("UniformBlueTex", width=64, height=64, alpha=False)
    pixels = []
    for _ in range(64 * 64):
        # RGBA per pixel, sRGB blue (R=0, G=0, B=1).
        pixels += [0.0, 0.0, 1.0, 1.0]
    img.pixels = pixels
    img.update()

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (-260, 0)
    tex.image = img

    nt.links.new(tex.outputs["Color"], hsv.inputs["Color"])
    nt.links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    # Smart UV unwrap on the sphere so the bake has 1:1 UVs.
    sphere.data.materials.append(mat)
    bpy.ops.object.select_all(action="DESELECT")
    sphere.select_set(True)
    bpy.context.view_layer.objects.active = sphere
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.sphere_project(direction="VIEW_ON_EQUATOR", align="POLAR_ZX")
    bpy.ops.object.mode_set(mode="OBJECT")

    return sphere, mat


def render_cycles(out_png):
    scn = bpy.context.scene
    scn.render.engine = "CYCLES"
    scn.cycles.device = "CPU"
    scn.cycles.samples = 64
    scn.cycles.use_denoising = True
    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGB"
    scn.render.use_stamp = False
    scn.render.filepath = out_png
    bpy.ops.render.render(write_still=True)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    sphere, mat = build_scene()

    blend_path = os.path.join(OUT_DIR, "bake_unrep_fixture.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    # Cycles reference render.
    cycles_png = os.path.join(OUT_DIR, "render_cycles_reference.png")
    render_cycles(cycles_png)
    print(f"[fixture] cycles reference -> {cycles_png}", flush=True)

    # Bake + export USD.
    usd_path = os.path.join(OUT_DIR, "bake_unrep_fixture_baked.usda")
    baked = bake_and_export(
        usd_path,
        bake_resolution=512,
        generate_preview_surface=True,
        generate_materialx_network=False,
        selected_objects_only=False,
        export_animation=False,
    )
    print(f"[fixture] baked map: {baked}", flush=True)
    assert "FixtureMat" in baked, f"expected FixtureMat baked, got {baked}"

    # Structural confirmation that the rewire happened.
    from pxr import Usd, UsdShade  # type: ignore
    stage = Usd.Stage.Open(usd_path)
    mat_prim = None
    for p in stage.Traverse():
        if p.IsA(UsdShade.Material) and p.GetName() in ("FixtureMat", "FixtureMat_"):
            mat_prim = UsdShade.Material(p)
            break
    assert mat_prim is not None, "FixtureMat material missing in USD"
    for child in mat_prim.GetPrim().GetChildren():
        if not child.IsA(UsdShade.Shader):
            continue
        sh = UsdShade.Shader(child)
        if sh.GetIdAttr().Get() == "UsdPreviewSurface":
            src = sh.GetInput("diffuseColor").GetConnectedSource()
            assert src is not None, "fixture diffuseColor not connected"
            src_shader = UsdShade.Shader(src[0].GetPrim())
            assert src_shader.GetIdAttr().Get() == "UsdUVTexture", (
                f"diffuseColor source not UsdUVTexture: {src_shader.GetIdAttr().Get()}"
            )
            print(f"[fixture] PASS structural: diffuseColor -> UsdUVTexture", flush=True)
            break

    print("[fixture] DONE", flush=True)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:
        print(f"[fixture] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        rc = 1
    sys.exit(rc)
