"""Fair, fully-representable Cycles-vs-Karma fixture for
`bl-usd-traverse-channel-misattribution:creature-body`.

WHY A SYNTHETIC FIXTURE FOR THE *VISUAL* COMPARISON
---------------------------------------------------
The structural proof of this fix lives in the sibling real-asset test
(`test_miris_bl_usd_traverse_channel_misattribution_creature_body.py`, driven by
swarmfish-v001.blend) and is authoritative. But the swarmfish hero asset is a
fundamentally UNFAIR *visual* fixture: its true albedo
(`background_creatures_diffuse_<UDIM>.tif`) is reachable from Base Color only as a
Vector-Math scale operand (UV distortion), never as a direct color source, so it
is genuinely unrepresentable as a single UsdPreviewSurface texture. Post-fix the
body's diffuseColor correctly falls back to the authored BSDF constant -> Karma
renders a flat untextured body while Cycles renders the painterly magenta. An
independent visual auditor sees "color missing" and fails the comparison even
though the fix is correct. The hero asset can therefore NEVER produce an
apples-to-apples color match.

This harness builds the SAME bug topology on an isolated UV sphere, but with the
diffuse intent expressed as a representable authored constant (magenta). The
material reproduces the exact misattribution path:

    Base Color  <- Reroute <- Hue/Saturation <- Mix(A=magenta, B=magenta)
    Mix.Factor  <- Map Range <- Image Texture (the ONLY image texture: a control
                                wire carrying a high-contrast checker pattern)
    Roughness   <- Image Texture            (the texture's legitimate home)

Base Color socket default_value is authored to magenta, and Mix selects magenta on
both branches, so Cycles renders a flat magenta sphere regardless of the
roughness-driven Factor.

  - STOCK export   : the naive DFS slides through Mix.Factor -> Map Range and
                     latches the lone Image Texture onto diffuseColor. Karma
                     paints the checker pattern as albedo -- the misattribution,
                     plainly visible and WRONG.
  - PATCHED export : traverse_channel halts at the Hue/Saturation modifier
                     (`is_signal_carrying_input` refuses to descend it) and falls
                     back to the authored magenta constant. Karma renders a flat
                     magenta sphere that MATCHES the Cycles reference.

Everything here is representable, so all three renders are directly comparable:
Cycles == patched-Karma (magenta), stock-Karma == wrong (checker on albedo).

Usage (build scene + Cycles ref + export USD + Karma, per binary role):
  <BINARY> --background --python render_miris_traverse_channel_fair_fixture.py \
      -- <out_dir> <role>            role = "patched" | "stock"

  patched -> render_cycles_reference.png + fixture-patched.usda + render_karma_postfix.png
  stock   -> fixture-stock.usda + render_karma_prefix.png  (no Cycles; geometry identical)

The Karma step shells out to hython/husk (Houdini paths below). If husk is
missing the harness still emits the Cycles reference + USD + structural assertion.
"""

import json
import math
import os
import subprocess
import sys

import bpy

HYTHON = (
    "/Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/"
    "Versions/Current/Resources/bin/hython"
)
HUSK = (
    "/Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/"
    "Versions/Current/Resources/bin/husk"
)

MAGENTA = (0.92, 0.04, 0.52, 1.0)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def make_control_texture(out_dir):
    """Author a high-contrast checker PNG on disk and return its path. This is the
    single Image Texture in the fixture -- its legitimate role is Roughness, and
    the stock DFS misattributes it onto diffuseColor via the Mix.Factor control
    wire."""
    w = h = 256
    img = bpy.data.images.new("control_checker", width=w, height=h, alpha=False)
    px = [0.0] * (w * h * 4)
    for y in range(h):
        for x in range(w):
            # 8x8 checker, with a colored tint so the leak is unmistakable on albedo.
            cell = ((x // 32) + (y // 32)) % 2
            r, g, b = (0.95, 0.85, 0.10) if cell else (0.05, 0.45, 0.85)
            i = (y * w + x) * 4
            px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, 1.0
    img.pixels.foreach_set(px)
    path = os.path.join(out_dir, "control_checker.png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    return path


def build_fixture(out_dir):
    reset_scene()
    tex_path = make_control_texture(out_dir)

    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.0)
    sphere = bpy.context.active_object
    bpy.ops.object.shade_smooth()

    mat = bpy.data.materials.new("traverse_fixture")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    # The one and only Image Texture (data, non-color).
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(tex_path)
    tex.image.colorspace_settings.name = "Non-Color"

    # Legitimate home: drives Roughness.
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])

    # Base Color chain: Reroute <- HueSat <- Mix(A=B=magenta); Mix.Factor is the
    # CONTROL wire carrying the texture (via Map Range) that stock leaks onto diffuse.
    maprange = nt.nodes.new("ShaderNodeMapRange")
    nt.links.new(tex.outputs["Color"], maprange.inputs["Value"])

    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = MAGENTA
    mix.inputs["B"].default_value = MAGENTA
    nt.links.new(maprange.outputs["Result"], mix.inputs["Factor"])

    hsv = nt.nodes.new("ShaderNodeHueSaturation")  # identity defaults
    nt.links.new(mix.outputs["Result"], hsv.inputs["Color"])

    reroute = nt.nodes.new("NodeReroute")
    nt.links.new(hsv.outputs["Color"], reroute.inputs[0])
    nt.links.new(reroute.outputs[0], bsdf.inputs["Base Color"])

    # Authored constant the PATCHED writer falls back to when it halts at HueSat.
    bsdf.inputs["Base Color"].default_value = MAGENTA

    sphere.data.materials.append(mat)
    return sphere


def add_camera_lights_world(sphere):
    import mathutils

    cam_data = bpy.data.cameras.new("RC")
    cam = bpy.data.objects.new("RC", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = mathutils.Vector((2.6, -3.4, 1.8))
    cam.rotation_euler = (
        mathutils.Vector((0, 0, 0)) - cam.location
    ).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    sun = bpy.data.objects.new("K", bpy.data.lights.new("K", type="SUN"))
    sun.data.energy = 4.0
    sun.data.angle = math.radians(5)
    sun.rotation_euler = (math.radians(45), math.radians(15), math.radians(40))
    bpy.context.collection.objects.link(sun)
    fill = bpy.data.objects.new("F", bpy.data.lights.new("F", type="SUN"))
    fill.data.energy = 1.2
    fill.rotation_euler = (math.radians(-25), math.radians(-30), math.radians(-150))
    bpy.context.collection.objects.link(fill)

    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    wnt = world.node_tree
    for n in list(wnt.nodes):
        wnt.nodes.remove(n)
    bg = wnt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.16, 0.18, 0.22, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    wout = wnt.nodes.new("ShaderNodeOutputWorld")
    wnt.links.new(bg.outputs["Background"], wout.inputs["Surface"])
    bpy.context.scene.world = world
    return cam, cam_data


def render_cycles(out_png, cam, cam_data):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = True
    sc.render.resolution_x = 800
    sc.render.resolution_y = 600
    sc.render.image_settings.file_format = "PNG"
    sc.render.use_stamp = False
    sc.render.filepath = out_png
    sc.view_settings.view_transform = "Standard"
    bpy.ops.render.render(write_still=True)
    return {
        "camera_world_matrix": [list(r) for r in cam.matrix_world],
        "lens_mm": cam_data.lens,
        "sensor_width": cam_data.sensor_width,
        "sensor_height": cam_data.sensor_height,
        "resolution": [800, 600],
    }


def export_usd(usd_path):
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


def report_diffuse(usd_path):
    """Print which texture (if any) diffuseColor resolves to -- the visible variable."""
    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(usd_path)
    surf = None
    for prim in stage.Traverse():
        sh = UsdShade.Shader(prim)
        if sh and sh.GetIdAttr().Get() == "UsdPreviewSurface":
            surf = sh
            break
    if not surf:
        print("MIRIS_FIXTURE diffuse=<no UsdPreviewSurface>")
        return None
    inp = surf.GetInput("diffuseColor")
    srcs = inp.GetConnectedSources() if inp else None
    if not srcs or not srcs[0]:
        const = inp.Get() if inp else None
        print(f"MIRIS_FIXTURE diffuseColor=CONSTANT {const}")
        return None
    src_prim = stage.GetPrimAtPath(srcs[0][0].source.GetPath())
    ss = UsdShade.Shader(src_prim)
    f = ss.GetInput("file") if ss else None
    a = f.Get() if f else None
    base = os.path.basename(str(a.path)) if a else "<unknown>"
    print(f"MIRIS_FIXTURE diffuseColor=TEXTURE {base}")
    return base


_KARMA_SRC = r'''
import sys, os, json, subprocess
from pxr import Usd, UsdGeom, UsdLux, Sdf, Gf, UsdRender
HUSK = sys.argv[1]; asset = os.path.abspath(sys.argv[2]); out = os.path.abspath(sys.argv[3]); cam_json = sys.argv[4]
cam = json.load(open(cam_json)); layer = out.replace(".png", ".usda")
st = Usd.Stage.CreateNew(layer)
UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z); UsdGeom.SetStageMetersPerUnit(st, 1.0)
ax = UsdGeom.Xform.Define(st, "/AssetRef")
ax.GetPrim().GetReferences().AddReference(os.path.relpath(asset, os.path.dirname(layer)))
st.SetDefaultPrim(ax.GetPrim())
m = Gf.Matrix4d(*[v for row in cam["camera_world_matrix"] for v in row]).GetTranspose()
cp = UsdGeom.Camera.Define(st, "/cameras/RenderCam")
xf = UsdGeom.Xformable(cp.GetPrim()); xf.ClearXformOpOrder(); xf.AddTransformOp().Set(m)
cp.GetFocalLengthAttr().Set(cam["lens_mm"]); cp.GetHorizontalApertureAttr().Set(cam["sensor_width"])
cp.GetVerticalApertureAttr().Set(cam["sensor_height"]); cp.GetClippingRangeAttr().Set(Gf.Vec2f(0.01, 1000.0))
k = UsdLux.DistantLight.Define(st, "/lights/Key"); k.GetIntensityAttr().Set(4.0); k.GetAngleAttr().Set(5.0)
UsdGeom.Xformable(k.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(45, 15, 40))
fl = UsdLux.DistantLight.Define(st, "/lights/Fill"); fl.GetIntensityAttr().Set(1.2); fl.GetAngleAttr().Set(5.0)
UsdGeom.Xformable(fl.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(-25, -30, -150))
dm = UsdLux.DomeLight.Define(st, "/lights/Dome"); dm.GetIntensityAttr().Set(0.25); dm.GetColorAttr().Set(Gf.Vec3f(0.16, 0.18, 0.22))
rs = UsdRender.Settings.Define(st, "/Render/rs"); rs.CreateResolutionAttr(Gf.Vec2i(800, 600)); rs.CreateCameraRel().SetTargets(["/cameras/RenderCam"])
rv = UsdRender.Var.Define(st, "/Render/rs/Var/Color"); rv.CreateDataTypeAttr("color3f"); rv.CreateSourceNameAttr("color"); rv.CreateSourceTypeAttr(UsdRender.Tokens.raw)
rv.GetPrim().CreateAttribute("driver:parameters:aov:husk:name", Sdf.ValueTypeNames.String).Set("color")
rv.GetPrim().CreateAttribute("driver:parameters:aov:name", Sdf.ValueTypeNames.String).Set("color")
rp = UsdRender.Product.Define(st, "/Render/rs/Product"); rp.CreateProductNameAttr("out.png"); rp.CreateCameraRel().SetTargets(["/cameras/RenderCam"])
rp.CreateOrderedVarsRel().SetTargets(["/Render/rs/Var/Color"]); rs.CreateProductsRel().SetTargets(["/Render/rs/Product"])
st.GetRootLayer().Save()
cmd = [HUSK, "--renderer", "BRAY_HdKarma", "--output", out, "--res", "800", "600", "--pixel-samples", "16",
       "--frame", "1", "--frame-count", "1", "--make-output-path", "--verbose", "a2", layer]
p = subprocess.run(cmd, capture_output=True, text=True)
open(out.replace(".png", ".husklog.txt"), "w").write(p.stdout + "\n" + p.stderr)
print("HUSK rc=%d -> %s" % (p.returncode, out))
'''


def render_karma(asset_usd, out_png, cam_json):
    if not os.path.exists(HUSK):
        print("MIRIS_KARMA_SKIPPED: husk not found at", HUSK)
        return
    script = os.path.join(os.path.dirname(out_png), "_karma_fixture_layer.py")
    open(script, "w").write(_KARMA_SRC)
    proc = subprocess.run(
        [HYTHON, script, HUSK, asset_usd, out_png, cam_json],
        capture_output=True,
        text=True,
    )
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr[-1500:])


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    out_dir = argv[0] if argv else os.getcwd()
    role = argv[1] if len(argv) > 1 else "patched"
    os.makedirs(out_dir, exist_ok=True)

    sphere = build_fixture(out_dir)
    cam, cam_data = add_camera_lights_world(sphere)
    cam_json = os.path.join(out_dir, "cam.json")

    if role == "patched":
        cam_meta = render_cycles(
            os.path.join(out_dir, "render_cycles_reference.png"), cam, cam_data
        )
        json.dump(cam_meta, open(cam_json, "w"), indent=2)
        usd = os.path.join(out_dir, "fixture-patched.usda")
        export_usd(usd)
        print("MIRIS_EXPORTED_USD:", usd)
        report_diffuse(usd)
        render_karma(usd, os.path.join(out_dir, "render_karma_postfix.png"), cam_json)
    else:
        # Geometry/camera identical; reuse cam.json produced by the patched pass.
        usd = os.path.join(out_dir, "fixture-stock.usda")
        export_usd(usd)
        print("MIRIS_EXPORTED_USD:", usd)
        report_diffuse(usd)
        if os.path.exists(cam_json):
            render_karma(usd, os.path.join(out_dir, "render_karma_prefix.png"), cam_json)
        else:
            print("MIRIS_KARMA_SKIPPED: cam.json missing (run patched role first)")
    print("MIRIS_FIXTURE_DONE role=%s dir=%s" % (role, out_dir))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
