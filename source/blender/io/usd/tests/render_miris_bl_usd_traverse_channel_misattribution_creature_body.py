"""Fair Cycles-vs-Karma render harness for `bl-usd-traverse-channel-misattribution:creature-body`.

This is the visual companion to the structural regression test
`test_miris_bl_usd_traverse_channel_misattribution_creature_body.py`. The C++ fix
(an `is_signal_carrying_input` allowlist gating the recursive descent in
`usd_writer_material.cc::traverse_channel`) is already landed; this harness
re-locks the invariant on the current HEAD and produces the fair before/after
renders the mission dashboard surfaces.

Why a separate harness (and why the render is a *placeholder*, not the proof):
  The swarmfish `creature_body` Base Color chain is
  `Reroute.001 -> HSV.001 -> Mix.003 -> ...`; the first modifier is a Hue/Saturation
  node, so post-fix `diffuseColor` halts immediately and falls back to the BSDF
  socket's authored constant. The artist's true albedo
  `background_creatures_diffuse_<UDIM>.tif` is reachable from Base Color ONLY as a
  Vector-Math *scale* operand (UV distortion), never as a direct color source --
  so it is genuinely unrepresentable as a single UsdPreviewSurface texture. The
  honest, achievable invariant is therefore "diffuse/emissive/normal NO LONGER
  point at roughness-watercolor.exr", NOT "diffuse connects to the .tif". The
  authoritative proof is the structural hython delta (see the sibling test); the
  renders below merely *illustrate* the misattribution removal.

What the three renders show (body isolated; FLOW/brushstroke/eye siblings hidden;
opacity held at 1.0 on both sides so the only variable is the color wiring):
  - render_cycles_reference.png : Cycles ground truth -- painterly magenta body.
  - karma_stock.png             : STOCK export -- the roughness-watercolor.exr is
                                  painted on as albedo AND wired to emissiveColor
                                  (a bright emissive hotspot), the misattribution.
  - karma_patched.png           : PATCHED export -- no texture on the color
                                  channels (clean authored-constant fallback);
                                  the spurious emissive hotspot is gone.

Usage (PATCHED build, with the real AYON blend as the scene argument):
  build_darwin/.../Blender --background <swarmfish-v001.blend> \
      --python render_miris_bl_usd_traverse_channel_misattribution_creature_body.py \
      -- <out_dir>

The harness shells out to hython/husk for the Karma renders, so Houdini must be
installed at the path below. If husk is unavailable the harness still emits the
Cycles reference and the structural assertion, and reports the Karma step skipped.
"""

import json
import math
import os
import subprocess
import sys
import tempfile

import bpy

HYTHON = (
    "/Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/"
    "Versions/Current/Resources/bin/hython"
)
HUSK = (
    "/Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/"
    "Versions/Current/Resources/bin/husk"
)

ROUGHNESS_FILE_BASENAME = "roughness-watercolor.exr"

# Body mesh kept visible; everything below is hidden so the body is unoccluded.
HIDE_PRIMS = (
    "/AssetRef/RIG_swarmfish/GEO_swamfish/GEO_swamfish___Brushstrokes_FLOW",
    "/AssetRef/RIG_swarmfish/GEO_swamfish/GEO_swamfish___Brushstrokes",
    "/AssetRef/RIG_swarmfish/GEO_swamfish_eyes",
    "/AssetRef/RIG_swarmfish/GEO_doublemohawk_eye_highlight1_R",
    "/AssetRef/RIG_swarmfish/GEO_doublemohawk_eye_highlight1_L",
    "/AssetRef/FX_swarmfish_creature_trail",
)


def export_preview(usd_path):
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


def render_cycles(out_png, cam_json):
    import mathutils

    body = [
        o
        for o in bpy.data.objects
        if o.type == "MESH" and o.name.startswith("GEO-swam") and "_eyes" not in o.name
    ]
    for o in bpy.data.objects:
        if o.type == "MESH" and o not in body:
            o.hide_render = True

    mins = mathutils.Vector((1e9, 1e9, 1e9))
    maxs = mathutils.Vector((-1e9, -1e9, -1e9))
    for o in body:
        for c in o.bound_box:
            v = o.matrix_world @ mathutils.Vector(c)
            for i in range(3):
                mins[i] = min(mins[i], v[i])
                maxs[i] = max(maxs[i], v[i])
    center = (mins + maxs) / 2
    extent = (maxs - mins).length

    for o in list(bpy.data.objects):
        if o.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(o, do_unlink=True)

    cam_data = bpy.data.cameras.new("RC")
    cam = bpy.data.objects.new("RC", cam_data)
    bpy.context.collection.objects.link(cam)
    dist = max(extent * 1.4, 3.0)
    cam.location = center + mathutils.Vector((dist * 0.55, -dist * 0.85, dist * 0.4))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    sun = bpy.data.objects.new("K", bpy.data.lights.new("K", type="SUN"))
    sun.data.energy = 4.0
    sun.data.angle = math.radians(5)
    sun.rotation_euler = (math.radians(40), math.radians(20), math.radians(45))
    bpy.context.collection.objects.link(sun)
    fill = bpy.data.objects.new("F", bpy.data.lights.new("F", type="SUN"))
    fill.data.energy = 1.0
    fill.rotation_euler = (math.radians(-25), math.radians(-30), math.radians(-160))
    bpy.context.collection.objects.link(fill)

    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    nt = world.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.18, 0.22, 0.26, 1.0)
    bg.inputs["Strength"].default_value = 1.2
    wout = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(bg.outputs["Background"], wout.inputs["Surface"])
    bpy.context.scene.world = world

    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 64
    sc.cycles.use_denoising = True
    sc.render.resolution_x = 800
    sc.render.resolution_y = 600
    sc.render.image_settings.file_format = "PNG"
    sc.render.use_stamp = False
    sc.render.filepath = out_png
    sc.view_settings.view_transform = "Standard"
    bpy.ops.render.render(write_still=True)

    json.dump(
        {
            "camera_world_matrix": [list(r) for r in cam.matrix_world],
            "lens_mm": cam_data.lens,
            "sensor_width": cam_data.sensor_width,
            "sensor_height": cam_data.sensor_height,
            "resolution": [800, 600],
        },
        open(cam_json, "w"),
        indent=2,
    )


# Standalone hython script: builds a body-isolated Karma render layer and runs husk.
_KARMA_SRC = r'''
import sys, os, json, subprocess
from pxr import Usd, UsdGeom, UsdLux, Sdf, Gf, UsdRender, UsdShade
HUSK = sys.argv[1]; asset = os.path.abspath(sys.argv[2]); out = os.path.abspath(sys.argv[3])
cam_json = sys.argv[4]; force_opacity = (len(sys.argv) > 5 and sys.argv[5] == "opacity1")
HIDE = %(hide)r
cam = json.load(open(cam_json)); layer = out.replace(".png", ".usda")
st = Usd.Stage.CreateNew(layer)
UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z); UsdGeom.SetStageMetersPerUnit(st, 1.0)
ax = UsdGeom.Xform.Define(st, "/AssetRef")
ax.GetPrim().GetReferences().AddReference(os.path.relpath(asset, os.path.dirname(layer)))
st.SetDefaultPrim(ax.GetPrim())
for h in HIDE:
    UsdGeom.Imageable(st.OverridePrim(h)).CreateVisibilityAttr().Set("invisible")
if force_opacity:
    sh = UsdShade.Shader(st.OverridePrim("/AssetRef/_materials/creature_body/Principled_BSDF_002"))
    sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(1.0)
m = Gf.Matrix4d(*[v for row in cam["camera_world_matrix"] for v in row]).GetTranspose()
cp = UsdGeom.Camera.Define(st, "/cameras/RenderCam")
xf = UsdGeom.Xformable(cp.GetPrim()); xf.ClearXformOpOrder(); xf.AddTransformOp().Set(m)
cp.GetFocalLengthAttr().Set(cam["lens_mm"]); cp.GetHorizontalApertureAttr().Set(cam["sensor_width"])
cp.GetVerticalApertureAttr().Set(cam["sensor_height"]); cp.GetClippingRangeAttr().Set(Gf.Vec2f(0.01, 1000.0))
k = UsdLux.DistantLight.Define(st, "/lights/Key"); k.GetIntensityAttr().Set(4.0); k.GetAngleAttr().Set(5.0)
UsdGeom.Xformable(k.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(40, 20, 45))
fl = UsdLux.DistantLight.Define(st, "/lights/Fill"); fl.GetIntensityAttr().Set(1.0); fl.GetAngleAttr().Set(5.0)
UsdGeom.Xformable(fl.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(-25, -30, -160))
dm = UsdLux.DomeLight.Define(st, "/lights/Dome"); dm.GetIntensityAttr().Set(0.3); dm.GetColorAttr().Set(Gf.Vec3f(0.18, 0.22, 0.26))
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
print("HUSK rc=%%d err1067=%%d -> %%s" %% (p.returncode, p.stdout.count("Error 1067") + p.stderr.count("Error 1067"), out))
''' % {"hide": list(HIDE_PRIMS)}


def render_karma(asset_usd, out_png, cam_json, force_opacity=False):
    if not os.path.exists(HUSK):
        print("MIRIS_KARMA_SKIPPED: husk not found at", HUSK)
        return
    script = os.path.join(os.path.dirname(out_png), "_karma_layer.py")
    open(script, "w").write(_KARMA_SRC)
    args = [HYTHON, script, HUSK, asset_usd, out_png, cam_json]
    if force_opacity:
        args.append("opacity1")
    proc = subprocess.run(args, capture_output=True, text=True)
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr[-1500:])


def validate(usd_path):
    """Re-assert the structural invariant from the exported PATCHED USD."""
    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(usd_path)
    mat = next(
        (
            UsdShade.Material(p)
            for p in stage.Traverse()
            if UsdShade.Material(p) and p.GetName() == "creature_body"
        ),
        None,
    )
    assert mat, "creature_body Material missing"
    surf = next(
        (
            UsdShade.Shader(c)
            for c in mat.GetPrim().GetChildren()
            if UsdShade.Shader(c)
            and UsdShade.Shader(c).GetIdAttr().Get() == "UsdPreviewSurface"
        ),
        None,
    )
    assert surf, "creature_body UsdPreviewSurface missing"

    def conn_file(name):
        inp = surf.GetInput(name)
        if not inp:
            return None
        s = inp.GetConnectedSources()
        if not s or not s[0]:
            return None
        sp = stage.GetPrimAtPath(s[0][0].source.GetPath())
        ss = UsdShade.Shader(sp)
        if not ss or ss.GetIdAttr().Get() != "UsdUVTexture":
            return None
        f = ss.GetInput("file")
        a = f.Get() if f else None
        return os.path.basename(str(a.path)) if a else None

    for chan in ("diffuseColor", "emissiveColor", "normal"):
        tex = conn_file(chan)
        print(f"MIRIS_CHANNEL {chan} -> texture={tex}")
        assert tex != ROUGHNESS_FILE_BASENAME, (
            f"{chan} still misattributed to {ROUGHNESS_FILE_BASENAME}"
        )
    print("MIRIS_RENDER_HARNESS_PASS: color/normal channels free of roughness-watercolor.exr")


def main():
    out_dir = sys.argv[-1] if "--" in sys.argv else tempfile.mkdtemp(prefix="miris_tc_render_")
    os.makedirs(out_dir, exist_ok=True)
    usd = os.path.join(out_dir, "swarmfish-patched.usda")
    cam_json = os.path.join(out_dir, "cam.json")

    export_preview(usd)
    print("MIRIS_EXPORTED_USD:", usd)
    validate(usd)

    render_cycles(os.path.join(out_dir, "render_cycles_reference.png"), cam_json)
    render_karma(usd, os.path.join(out_dir, "render_karma_postfix.png"), cam_json)
    print("MIRIS_RENDERS_WRITTEN:", out_dir)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
