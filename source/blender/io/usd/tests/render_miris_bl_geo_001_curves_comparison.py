"""BL-GEO-001 fair visual-comparison harness (Cycles reference vs Karma USD).

The structural proof for BL-GEO-001 lives in
``test_miris_bl_geo_001_gn_curves_per_curve_material_index.py`` (asserts the per-curve
material_index round-trips to two materialBind UsdGeomSubsets). Neither Cycles nor Karma
honors curve-domain GeomSubset bindings at render time -- both render the whole-prim
(slot-0) binding -- so the *render* can only demonstrate that the multi-curve carrier
geometry round-trips faithfully Blender -> USD -> Karma. This harness produces that
apples-to-apples comparison.

Prior render attempt failed an independent visual audit ("six strokes collapsed into two
edge blobs"). Root cause was NOT the exporter fix: the Karma render layer received an
*identity* camera matrix because ``camera.matrix_world`` was read before the depsgraph
evaluated the object transform, dropping the render camera into the middle of the curve
cloud. The fix is the ``bpy.context.view_layer.update()`` below before reading the matrix.

Design choices that make the comparison fair:
  * isolated 6-curve fixture (material_index [0,0,0,1,1,1]) -- the feature fills the frame,
    nothing occludes it;
  * front-on camera framing all six bars, matched between Cycles and the exported USD;
  * emission-only material -- view/normal-independent, so a flat Cycles ribbon and a round
    Karma tube render the same uniform salmon (slot-0 matA), and exposure matches without
    fiddly light balancing;
  * Standard view transform on the Cycles side to avoid AgX desaturation.

Stage 1 (Blender, patched build):
    Blender --background --python this_file.py -- <out_usd> <cycles_png> <cam_json>
Stage 2 (hython, run from a clean cwd):
    hython render_karma.py <out_usd> <karma_png> <cam_json>
"""
import bpy, sys, os, json, math

argv = sys.argv[sys.argv.index("--") + 1:]
OUT_USD, RENDER_PNG, CAM_JSON = argv[0], argv[1], argv[2]

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene

NCURVES, PTS = 6, 8
curves = bpy.data.hair_curves.new("BLGEO001_multimat")
curves.add_curves([PTS] * NCURVES)
pos = curves.attributes["position"]
i = 0
for c in range(NCURVES):
    x = -2.5 + c * 1.0
    for p in range(PTS):
        z = -1.2 + (2.4 * p / (PTS - 1))
        pos.data[i].vector = (x, 0.0, z)
        i += 1

rad = curves.attributes["radius"] if "radius" in curves.attributes \
    else curves.attributes.new("radius", "FLOAT", "POINT")
for d in rad.data:
    d.value = 0.16
ct = curves.attributes.new("curve_type", "INT8", "CURVE")
for d in ct.data:
    d.value = 0  # poly
mi = curves.attributes.new("material_index", "INT", "CURVE")
for k, d in enumerate(mi.data):
    d.value = 0 if k < NCURVES // 2 else 1


def emat(name, rgb):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0, 0, 0, 1)
    bsdf.inputs["Emission Color"].default_value = (*rgb, 1)
    bsdf.inputs["Emission Strength"].default_value = 1.0
    return m


matA = emat("BLGEO001_matA", (0.96, 0.45, 0.40))  # salmon (slot 0 = what renders)
matB = emat("BLGEO001_matB", (0.20, 0.55, 0.90))  # structural-only second material
curves.materials.append(matA)
curves.materials.append(matB)
obj = bpy.data.objects.new("BLGEO001_multimat", curves)
sc.collection.objects.link(obj)

cam_data = bpy.data.cameras.new("Cam")
cam = bpy.data.objects.new("Cam", cam_data)
sc.collection.objects.link(cam)
cam.location = (0.0, -8.0, 0.0)
cam.rotation_euler = (math.radians(90), 0, 0)
cam_data.lens = 50
cam_data.sensor_width = 36
sc.camera = cam

w = bpy.data.worlds.new("W")
sc.world = w
w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.02, 0.025, 1)
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0

RES_X, RES_Y = 800, 600
bpy.context.view_layer.update()  # CRITICAL: eval transforms so matrix_world isn't identity
mw = cam.matrix_world
json.dump({
    "camera_world_matrix": [[mw[r][c] for c in range(4)] for r in range(4)],
    "lens_mm": cam_data.lens,
    "sensor_width": cam_data.sensor_width,
    "sensor_height": cam_data.sensor_width * RES_Y / RES_X,
    "resolution": [RES_X, RES_Y],
}, open(CAM_JSON, "w"), indent=2)

sc.render.engine = 'CYCLES'
sc.cycles.device = 'CPU'
sc.cycles.samples = 96
sc.render.resolution_x = RES_X
sc.render.resolution_y = RES_Y
sc.render.image_settings.file_format = 'PNG'
sc.render.use_stamp = False
sc.view_settings.view_transform = 'Standard'
sc.render.filepath = RENDER_PNG
bpy.ops.render.render(write_still=True)

bpy.ops.wm.usd_export(
    filepath=OUT_USD, check_existing=False, selected_objects_only=False,
    export_hair=True, export_materials=True, evaluation_mode="RENDER",
    generate_preview_surface=True, root_prim_path="/root",
)
print("USD_EXPORT_PATH:", OUT_USD)
