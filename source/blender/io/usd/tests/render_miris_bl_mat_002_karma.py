"""BL-MAT-002 fair visual-comparison fixture (Karma/husk side).

Renders a USD exported by render_miris_bl_mat_002_fair_fixture.py with Karma, at the same
camera + lighting as that script's Cycles reference. No occlusion-hiding is needed because the
fixture scene contains only the three showcase spheres.

Usage: hython render_miris_bl_mat_002_karma.py <fixture.usd> <out.png>
The script reads <fixture>_cam.json (written by the Blender-side script) for the camera.
It prints the husk return code and the `Error 1067` log-count: the STOCK export logs >0
(eyes+pupil grey) and the PATCHED export logs 0 (eyes+pupil shaded).
"""
import sys, os, json, subprocess
from pxr import Usd, UsdGeom, UsdLux, Sdf, Gf, UsdRender

HUSK = "/Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/husk"

def build_layer(asset_usd, layer_path, cam_json):
    cam = json.load(open(cam_json))
    stage = Usd.Stage.CreateNew(layer_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    ref = UsdGeom.Xform.Define(stage, "/AssetRef")
    ref.GetPrim().GetReferences().AddReference(
        os.path.relpath(asset_usd, os.path.dirname(layer_path)))
    stage.SetDefaultPrim(ref.GetPrim())

    camp = UsdGeom.Camera.Define(stage, "/cameras/RenderCam")
    m = Gf.Matrix4d(*[v for row in cam["camera_world_matrix"] for v in row]).GetTranspose()
    xf = UsdGeom.Xformable(camp.GetPrim()); xf.ClearXformOpOrder(); xf.AddTransformOp().Set(m)
    camp.GetFocalLengthAttr().Set(cam["lens_mm"])
    camp.GetHorizontalApertureAttr().Set(cam["sensor_width"])
    camp.GetVerticalApertureAttr().Set(cam["sensor_height"])
    camp.GetClippingRangeAttr().Set(Gf.Vec2f(0.01, 1000.0))

    key = UsdLux.DistantLight.Define(stage, "/lights/KeySun")
    key.GetIntensityAttr().Set(4.0); key.GetAngleAttr().Set(5.0)
    UsdGeom.Xformable(key.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(40.0, 20.0, 45.0))
    fill = UsdLux.DistantLight.Define(stage, "/lights/Fill")
    fill.GetIntensityAttr().Set(1.0); fill.GetAngleAttr().Set(5.0)
    UsdGeom.Xformable(fill.GetPrim()).AddRotateXYZOp().Set(Gf.Vec3f(-25.0, -30.0, -160.0))
    dome = UsdLux.DomeLight.Define(stage, "/lights/SkyDome")
    dome.GetIntensityAttr().Set(0.8); dome.GetColorAttr().Set(Gf.Vec3f(0.55, 0.6, 0.7))

    rs = UsdRender.Settings.Define(stage, "/Render/rendersettings")
    rs.CreateResolutionAttr(Gf.Vec2i(*cam["resolution"]))
    rs.CreateCameraRel().SetTargets(["/cameras/RenderCam"])
    rv = UsdRender.Var.Define(stage, "/Render/rendersettings/Var/Color")
    rv.CreateDataTypeAttr("color3f"); rv.CreateSourceNameAttr("color")
    rv.CreateSourceTypeAttr(UsdRender.Tokens.raw)
    rv.GetPrim().CreateAttribute("driver:parameters:aov:husk:name", Sdf.ValueTypeNames.String).Set("color")
    rv.GetPrim().CreateAttribute("driver:parameters:aov:name", Sdf.ValueTypeNames.String).Set("color")
    rp = UsdRender.Product.Define(stage, "/Render/rendersettings/Product")
    rp.CreateProductNameAttr("out.png"); rp.CreateCameraRel().SetTargets(["/cameras/RenderCam"])
    rp.CreateOrderedVarsRel().SetTargets(["/Render/rendersettings/Var/Color"])
    rs.CreateProductsRel().SetTargets(["/Render/rendersettings/Product"])
    stage.GetRootLayer().Save()

def main():
    asset = os.path.abspath(sys.argv[1]); out = os.path.abspath(sys.argv[2])
    cam_json = asset.rsplit(".", 1)[0] + "_cam.json"
    layer = os.path.join(os.path.dirname(out),
                         "renderlayer-" + os.path.basename(out).replace(".png", "") + ".usda")
    build_layer(asset, layer, cam_json)
    cmd = [HUSK, "--renderer", "BRAY_HdKarma", "--output", out, "--res", "800", "600",
           "--pixel-samples", "16", "--frame", "1", "--frame-count", "1",
           "--make-output-path", "--verbose", "a2", layer]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log = out.replace(".png", ".husklog.txt")
    open(log, "w").write("STDOUT:\n" + proc.stdout + "\n\nSTDERR:\n" + proc.stderr)
    n1067 = (proc.stdout + proc.stderr).count("Error 1067")
    print(f"[husk] rc={proc.returncode} Error1067_count={n1067} log={log}", file=sys.stderr)
    return proc.returncode

sys.exit(main())
