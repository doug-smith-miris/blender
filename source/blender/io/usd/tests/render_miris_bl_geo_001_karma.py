import sys, os, json, subprocess
from pxr import Usd, UsdGeom, UsdLux, Sdf, Gf, UsdRender
HUSK="/Applications/Houdini/Houdini21.0.700/Frameworks/Houdini.framework/Versions/Current/Resources/bin/husk"
asset_usd=os.path.abspath(sys.argv[1]); out_png=os.path.abspath(sys.argv[2]); cam_json=sys.argv[3]
ci=json.load(open(cam_json))
layer=out_png.replace(".png",".layer.usda")
stage=Usd.Stage.CreateNew(layer)
UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z); UsdGeom.SetStageMetersPerUnit(stage,1.0)
ax=UsdGeom.Xform.Define(stage,"/AssetRef")
ax.GetPrim().GetReferences().AddReference(os.path.relpath(asset_usd,os.path.dirname(layer)))
stage.SetDefaultPrim(ax.GetPrim())
cam=UsdGeom.Camera.Define(stage,"/cameras/RenderCam")
m=Gf.Matrix4d(*[v for row in ci["camera_world_matrix"] for v in row]).GetTranspose()
xf=UsdGeom.Xformable(cam.GetPrim()); xf.ClearXformOpOrder(); xf.AddTransformOp().Set(m)
cam.GetFocalLengthAttr().Set(ci["lens_mm"]); cam.GetHorizontalApertureAttr().Set(ci["sensor_width"])
cam.GetVerticalApertureAttr().Set(ci["sensor_height"]); cam.GetClippingRangeAttr().Set(Gf.Vec2f(0.01,1000))
d=UsdLux.DomeLight.Define(stage,"/lights/Sky"); d.GetIntensityAttr().Set(0.1); d.GetColorAttr().Set(Gf.Vec3f(0.1,0.1,0.12))
rs=UsdRender.Settings.Define(stage,"/Render/rs"); rs.CreateResolutionAttr(Gf.Vec2i(*ci["resolution"])); rs.CreateCameraRel().SetTargets(["/cameras/RenderCam"])
rv=UsdRender.Var.Define(stage,"/Render/rs/Var/Color"); rv.CreateDataTypeAttr("color3f"); rv.CreateSourceNameAttr("color"); rv.CreateSourceTypeAttr(UsdRender.Tokens.raw)
rv.GetPrim().CreateAttribute("driver:parameters:aov:husk:name",Sdf.ValueTypeNames.String).Set("color")
rp=UsdRender.Product.Define(stage,"/Render/rs/Product"); rp.CreateProductNameAttr(out_png); rp.CreateCameraRel().SetTargets(["/cameras/RenderCam"]); rp.CreateOrderedVarsRel().SetTargets(["/Render/rs/Var/Color"])
rs.CreateProductsRel().SetTargets(["/Render/rs/Product"]); stage.GetRootLayer().Save()
cmd=[HUSK,"--renderer","BRAY_HdKarma","--output",out_png,"--res","800","600","--pixel-samples","24","--frame","1","--frame-count","1","--make-output-path","--verbose","a1",layer]
p=subprocess.run(cmd,capture_output=True,text=True)
open(out_png.replace(".png",".log"),"w").write(p.stdout+"\n"+p.stderr)
print("rc=",p.returncode,"->",out_png,file=sys.stderr)
sys.exit(p.returncode)
