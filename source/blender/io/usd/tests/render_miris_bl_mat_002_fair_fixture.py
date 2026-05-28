"""BL-MAT-002 fair visual-comparison fixture (Blender side).

The BL-MAT-002 exporter fix (post-UsdMtlxRead phantom-nodedef prune + ND_layer_ bypass,
committed in this lineage) is structurally verified by
test_miris_bl_mat_002_materialx_phantom_nodedef_prune.py. An independent visual audit of the
*full-asset* swarmfish comparison renders FAILED, however: the targeted materials were buried
behind the unbound `_Brushstrokes_FLOW` occluder (a separate BL-GEO finding) and the body's
`opacity=0.0` UsdPreviewSurface bug (a separate BL-alpha finding), so the frame read as
near-blank and the materials under test were unassessable. The failure was in the render
*setup*, not the fix.

This harness makes the comparison fair by isolating the real on-corpus phantom-nodedef
trigger materials onto frame-filling UV spheres in a clean scene, and exporting MaterialX-ONLY
(so the opacity=0.0 UsdPreviewSurface path cannot apply at all):

  * creature-eyes / creature-pupil  -> carry MaterialX `node_018`, whose nodedef the bundled
    USD MaterialX library cannot resolve (`Unable to find the nodedef for 'node_018'`). On the
    STOCK export these become `info:id`-less Shader stubs with dangling connections, so Karma
    aborts the whole shader collection with `Error 1067: Reference to undefined variable` and
    renders them FLAT GREY. The patched exporter prunes the phantom stubs, so they compile and
    render their full shaded iris -- matching the Cycles reference.
  * creature-body is included as an UNAFFECTED CONTROL: it carries no phantom nodedef, so it
    renders identically (purple) in both the stock and patched Karma frames. Its painterly
    base-color flatness vs Cycles is a separate traverse_channel finding (PR #25), not
    BL-MAT-002. (creature-BS / node_027 is excluded: it is a transparent brushstroke-FX
    material that renders to near-nothing on a sphere in BOTH renderers -- a poor witness.)

Run (BOTH binaries; the Cycles arg is exporter-independent so render it once on the patched):
  <blender> --background <swarmfish-v001.blend> --python render_miris_bl_mat_002_fair_fixture.py \
      -- <out.usd> [cycles_reference.png]

then render each exported USD with render_miris_bl_mat_002_karma.py (hython/husk). The stock
export renders eyes+pupil grey (Error 1067 > 0); the patched export renders them shaded
(Error 1067 == 0).
"""
import bpy, sys, json, math

argv = sys.argv[sys.argv.index("--") + 1:]
OUT_USD = argv[0]
CYCLES_PNG = argv[1] if len(argv) > 1 else None
CAM_JSON = OUT_USD.rsplit(".", 1)[0] + "_cam.json"

SHOWCASE = [
    ("creature-body",  (-3.0, 0.0, 0.0)),   # unaffected control (no phantom nodedef)
    ("creature-eyes",  ( 0.0, 0.0, 0.0)),   # node_018 phantom trigger
    ("creature-pupil", ( 3.0, 0.0, 0.0)),   # node_018 phantom trigger
]
RES = (800, 600)

# clean scene: only our spheres export/render
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

for name, loc in SHOWCASE:
    mat = bpy.data.materials.get(name)
    if mat is None:
        print(f"[fixture] WARNING material not found: {name}", file=sys.stderr)
        continue
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0, location=loc)
    sph = bpy.context.active_object
    sph.name = f"showcase_{name.replace('-', '_')}"
    bpy.ops.object.shade_smooth()
    sph.data.materials.clear()
    sph.data.materials.append(mat)
    print(f"[fixture] sphere {sph.name} <- {name}")

cam_data = bpy.data.cameras.new("FixtureCam")
cam_data.lens = 50.0
cam_data.sensor_width = 36.0
cam_obj = bpy.data.objects.new("FixtureCam", cam_data)
bpy.context.scene.collection.objects.link(cam_obj)
cam_obj.location = (0.0, -12.0, 0.0)
cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
bpy.context.scene.camera = cam_obj

def add_sun(name, energy, rot_deg):
    d = bpy.data.lights.new(name, 'SUN'); d.energy = energy; d.angle = math.radians(5)
    ob = bpy.data.objects.new(name, d); bpy.context.scene.collection.objects.link(ob)
    ob.rotation_euler = [math.radians(a) for a in rot_deg]
add_sun("Key", 4.0, (40, 20, 45))
add_sun("Fill", 1.0, (-25, -30, -160))
world = bpy.context.scene.world or bpy.data.worlds.new("W")
bpy.context.scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg:
    bg.inputs[0].default_value = (0.55, 0.6, 0.7, 1.0)
    bg.inputs[1].default_value = 0.8

bpy.context.view_layer.update()   # gotcha: matrix_world is identity until this runs
json.dump({
    "camera_world_matrix": [list(r) for r in cam_obj.matrix_world],
    "lens_mm": cam_data.lens,
    "sensor_width": cam_data.sensor_width,
    "sensor_height": cam_data.sensor_width * RES[1] / RES[0],
    "resolution": list(RES),
}, open(CAM_JSON, "w"), indent=2)
print(f"[fixture] cam dumped -> {CAM_JSON}")

if CYCLES_PNG:
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    sc.cycles.device = 'CPU'
    sc.cycles.samples = 64
    sc.render.resolution_x, sc.render.resolution_y = RES
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = 'PNG'
    sc.render.use_stamp = False
    sc.render.filepath = CYCLES_PNG
    bpy.ops.render.render(write_still=True)
    print(f"CYCLES_RENDER_PATH={CYCLES_PNG}")

for obj in bpy.data.objects:
    obj.select_set(obj.type == 'MESH')
bpy.ops.wm.usd_export(
    filepath=OUT_USD,
    check_existing=False,
    selected_objects_only=True,
    export_animation=False,
    export_uvmaps=True,
    export_normals=True,
    export_materials=True,
    evaluation_mode="RENDER",
    generate_preview_surface=False,     # MaterialX-only: opacity=0.0 confound cannot apply
    generate_materialx_network=True,
    convert_world_material=False,
    root_prim_path="/root",
)
print(f"USD_EXPORT_PATH={OUT_USD}")
