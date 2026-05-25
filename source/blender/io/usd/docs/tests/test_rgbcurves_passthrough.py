# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Round-trip test for the USD exporter's MaterialX translation of the
ShaderNodeRGBCurve node (finding BL-MAT-005-rgbcurves-silent-passthrough).

Reproduces the failure mode catalogued in
``Agent Builder/knowledge/usd-export-issues-catalog.md`` (BL-MAT-005) and
observed downstream of the swarmfish eye-material chain: the MaterialX
``materialx_fn`` for ``ShaderNodeRGBCurve`` (``node_shader_curves.cc``
``rgb::node_shader_materialx``) returned ``get_input_value("Color", ...)``
unchanged, dropping the curve and emitting only an
``ND_convert_color4_color3`` no-op into the exported network. Karma renders
the surface as if there were no curve adjustment at all, which is why the
``creature-eyes`` color grading silently disappeared in the prior pipeline
run.

The graph constructed here is the minimal repro: Output Material is fed by a
Principled BSDF whose Base Color is driven by a ShaderNodeRGBCurve. The RGB
curve has a strong, asymmetric non-identity shape — the Red channel has an
added knot pulling x=0.5 down to y=0.1 and the Green channel adds a knot at
x=0.5, y=0.9. A neutral grey input (0.5, 0.5, 0.5) is hard-wired into the
curve's Color input via a ShaderNodeRGB so the validator can read a known
expected output (R ≈ 0.10, G ≈ 0.90, B ≈ 0.50) directly off the network's
combine3 node's constant ``in1``/``in2``/``in3`` inputs.

Invocation:

    /Users/d.smith/MirisProjects/build_darwin/bin/Blender.app/Contents/MacOS/Blender \\
        --background \\
        --python source/blender/io/usd/docs/tests/test_rgbcurves_passthrough.py

Prints ``EXPORTED_USDA=<path>`` on its own line for the validation step
(``validate_rgbcurves_passthrough.py``) to pick up.
"""

import os
import sys
import tempfile

import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object
assert cube is not None, "Cube creation failed"

mat = bpy.data.materials.new(name="RGBCurvesMaterial")
mat.use_nodes = True
nt = mat.node_tree

# Strip default nodes except Output Material.
out = None
for node in list(nt.nodes):
    if node.type == "OUTPUT_MATERIAL":
        out = node
    else:
        nt.nodes.remove(node)
assert out is not None, "Default Output Material missing after reset"

# Principled BSDF — the surface shader the curve feeds into.
principled = nt.nodes.new("ShaderNodeBsdfPrincipled")
principled.location = (200, 0)
principled.inputs["Roughness"].default_value = 0.42

# RGB Curves — the node under test.
rgb_curves = nt.nodes.new("ShaderNodeRGBCurve")
rgb_curves.location = (-200, 0)

# Configure the mapping: shape Red downward at 0.5 (0.5 -> 0.1) and shape
# Green upward at 0.5 (0.5 -> 0.9). Blue is left as identity. The Combined
# (cm[3]) curve is also left as identity so the per-channel transformations
# are easy to reason about.
mapping = rgb_curves.mapping
# mapping.curves[0] = R, [1] = G, [2] = B, [3] = Combined.
r_curve = mapping.curves[0]
g_curve = mapping.curves[1]
# Each curve starts with two points at (0,0) and (1,1). Insert a middle knot
# that breaks the identity-detection heuristic
# (BKE_curvemapping_is_map_identity).
r_curve.points.new(0.5, 0.1)
g_curve.points.new(0.5, 0.9)
# The Blender API requires update() after editing curve points.
mapping.update()

# Constant RGB input — a neutral grey so the curve's evaluation at x=0.5
# gives the validator a known, easy-to-check expected output per channel.
const_rgb = nt.nodes.new("ShaderNodeRGB")
const_rgb.location = (-500, 0)
const_rgb.outputs[0].default_value = (0.5, 0.5, 0.5, 1.0)

# Wire RGB constant -> RGB Curves Color input -> Principled Base Color ->
# Output Material Surface.
nt.links.new(const_rgb.outputs["Color"], rgb_curves.inputs["Color"])
nt.links.new(rgb_curves.outputs["Color"], principled.inputs["Base Color"])
nt.links.new(principled.outputs["BSDF"], out.inputs["Surface"])

# Assign to the cube.
if cube.data.materials:
    cube.data.materials[0] = mat
else:
    cube.data.materials.append(mat)

# Export with the MaterialX network path — that is the path with the broken
# rgb::node_shader_materialx implementation.
export_path = os.path.join(tempfile.gettempdir(), "test_rgbcurves_passthrough.usda")
result = bpy.ops.wm.usd_export(
    filepath=export_path,
    export_materials=True,
    generate_materialx_network=True,
    selected_objects_only=False,
    evaluation_mode="RENDER",
)

assert "FINISHED" in result, f"USD export failed: {result}"
assert os.path.isfile(export_path), f"Export did not produce a file at {export_path}"

print(f"EXPORTED_USDA={export_path}")
sys.exit(0)
