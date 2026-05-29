"""
Isolated fair-fixture verification for critter-mtlx-faithful-bake.

Reproduces the *category* the bite targets:
   Principled BSDF wrapped inside a ShaderNodeGroup (mirroring critter's
   body_purple / body_mouth_bag, which wrap their BSDF in the
   `critter-body_surface` group), with Base Color reached through a
   color-modifying node (Hue/Saturation/Value). Stock PR #31 detection
   never finds the BSDF (it scans only the outer material tree); the
   extended detection recurses into the group, bakes Cycles' painterly
   diffuse to PNG, and rewires BOTH `UsdPreviewSurface.diffuseColor` and
   `mtlx outputs:mtlx:surface` to a fresh open_pbr_surface base_color so
   Karma (which only reads MaterialX per PR #35's default) shows the
   baked color, not the default fallback.

Fixture: UV-Sphere whose material wraps Image-Tex -> HSV(hue=0.7) ->
Principled BSDF inside a ShaderNodeGroup. The blue-into-yellow HSV
rotation gives a strong observable: a *yellow/orange* sphere when the
chain is captured, vs flat / magenta when it isn't.

Outputs (all in OUT_DIR):
    critter_fixture.blend                   -- the saved fixture scene
    critter_fixture_baked.usda              -- patched-pipeline USD
    critter_fixture_stock.usda              -- stock-pipeline USD (no bake)
    render_cycles_reference.png             -- Cycles ground truth
    render_karma_postfix.png                -- Karma against patched USD
    render_karma_prefix.png                 -- Karma against stock USD
"""
import math
import os
import sys

import bpy

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from miris_bake_unrepresentable_albedo import bake_and_export  # noqa: E402

OUT_DIR = "/tmp/critter_mtlx_fixture"
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
    scn.render.image_settings.file_format = "PNG"
    scn.render.image_settings.color_mode = "RGB"
    scn.render.use_stamp = False

    world = bpy.data.worlds.new("FixtureWorld")
    scn.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.55, 0.55, 0.55, 1.0)
    bg.inputs["Strength"].default_value = 1.0

    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=64, ring_count=32,
                                         location=(0.0, 0.0, 0.0))
    sphere = bpy.context.active_object
    bpy.ops.object.shade_smooth()

    bpy.ops.object.camera_add(location=(0.0, -3.2, 0.0),
                              rotation=(math.radians(90.0), 0.0, 0.0))
    cam = bpy.context.active_object
    scn.camera = cam
    cam.data.lens = 50.0

    # --- Inner ShaderNodeGroup tree (mirrors critter-body_surface idiom) ---
    inner_tree = bpy.data.node_groups.new("critter_like_surface", "ShaderNodeTree")
    # Interface: just one output socket of type Shader.
    inner_tree.interface.new_socket(name="Shader", in_out="OUTPUT",
                                    socket_type="NodeSocketShader")
    g_out = inner_tree.nodes.new("NodeGroupOutput")
    g_out.location = (600, 0)

    bsdf = inner_tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    bsdf.inputs["Specular IOR Level"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Metallic"].default_value = 0.0

    hsv = inner_tree.nodes.new("ShaderNodeHueSaturation")
    hsv.location = (40, 0)
    hsv.inputs["Hue"].default_value = 0.7
    hsv.inputs["Saturation"].default_value = 1.0
    hsv.inputs["Value"].default_value = 1.0
    hsv.inputs["Fac"].default_value = 1.0

    img = bpy.data.images.new("UniformBlueTex", width=64, height=64, alpha=False)
    pixels = []
    for _ in range(64 * 64):
        pixels += [0.0, 0.0, 1.0, 1.0]
    img.pixels = pixels
    img.update()

    tex = inner_tree.nodes.new("ShaderNodeTexImage")
    tex.location = (-260, 0)
    tex.image = img

    inner_tree.links.new(tex.outputs["Color"], hsv.inputs["Color"])
    inner_tree.links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])
    inner_tree.links.new(bsdf.outputs["BSDF"], g_out.inputs["Shader"])

    # --- Outer material wraps the group (this is what critter does) -------
    mat = bpy.data.materials.new("FixtureMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.node_tree = inner_tree
    grp.location = (200, 0)
    nt.links.new(grp.outputs["Shader"], out.inputs["Surface"])

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


def export_stock_usd(usd_path: str):
    bpy.ops.wm.usd_export(
        filepath=usd_path,
        generate_preview_surface=True,
        generate_materialx_network=True,
        emit_preview_surface_alongside_materialx=False,
        selected_objects_only=False,
        export_animation=False,
    )


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    sphere, mat = build_scene()

    blend_path = os.path.join(OUT_DIR, "critter_fixture.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    cycles_png = os.path.join(OUT_DIR, "render_cycles_reference.png")
    render_cycles(cycles_png)
    print(f"[fixture] cycles reference -> {cycles_png}", flush=True)

    # 1) STOCK pipeline: just wm.usd_export — no bake. Used as "prefix" baseline.
    stock_usd = os.path.join(OUT_DIR, "critter_fixture_stock.usda")
    export_stock_usd(stock_usd)
    print(f"[fixture] stock USD -> {stock_usd}", flush=True)

    # 2) PATCHED pipeline: bake + dual-arc rewire — the bite's contribution.
    patched_usd = os.path.join(OUT_DIR, "critter_fixture_baked.usda")
    baked = bake_and_export(
        patched_usd,
        bake_resolution=512,
        generate_preview_surface=True,
        generate_materialx_network=True,
        # Keep PR #35's production default (mtlx-only) so this proves the
        # MTLX rewire is the load-bearing channel for Karma.
        emit_preview_surface_alongside_materialx=False,
        selected_objects_only=False,
        export_animation=False,
    )
    print(f"[fixture] patched baked -> {baked}", flush=True)
    assert "FixtureMat" in baked, f"expected FixtureMat in baked, got {baked}"

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
