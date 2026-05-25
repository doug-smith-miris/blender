# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Round-trip test for the MaterialX export of the legacy ShaderNodeMixRGB node.

This is the test partner for the C++ change in
`source/blender/nodes/shader/nodes/node_shader_mix_rgb.cc` that adds
`NODE_SHADER_MATERIALX_BEGIN ... END` support. Validates the
``ShaderNodeMixRGB`` (legacy) → MaterialX ``mix`` mapping documented in
``source/blender/io/usd/docs/translation-mapping.md``.

Invocation (matches the architectural-mapping mission contract):

    /Applications/Blender.app/Contents/MacOS/Blender \\
        --background \\
        --python source/blender/io/usd/docs/tests/test_mix_rgb.py

The script prints the path of the exported .usda to stdout as a single line
prefixed with ``EXPORTED_USDA=``. The validation step (``validate_mix_rgb.py``)
then opens that .usda via hython and asserts the expected MaterialX shader is
present.
"""

import os
import sys
import tempfile

import bpy

# Clean slate: start from an empty scene so the only material on disk is the one
# we build below.
bpy.ops.wm.read_factory_settings(use_empty=True)

# Add a single cube to receive the material.
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object
assert cube is not None, "Cube creation failed"

# Build a material whose only non-output node is a *legacy* MixRGB node, fed by
# two RGB constants. The intent is to keep the entire shader graph inside the
# supported set so the exporter can emit a complete MaterialX network without
# falling back to bake-to-UsdPreviewSurface.
mat = bpy.data.materials.new(name="MixRGBLegacyMaterial")
mat.use_nodes = True
nt = mat.node_tree

# Clear the default Principled BSDF — we want a minimal graph.
for node in list(nt.nodes):
    if node.type != "OUTPUT_MATERIAL":
        nt.nodes.remove(node)

out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")

# Two RGB constants (color1, color2) feeding the MixRGB.
rgb_a = nt.nodes.new("ShaderNodeRGB")
rgb_a.outputs[0].default_value = (1.0, 0.0, 0.0, 1.0)  # pure red
rgb_a.location = (-600, 100)

rgb_b = nt.nodes.new("ShaderNodeRGB")
rgb_b.outputs[0].default_value = (0.0, 0.0, 1.0, 1.0)  # pure blue
rgb_b.location = (-600, -200)

# The legacy MixRGB node. ``ShaderNodeMixRGB`` is the legacy node type identifier
# in bpy's API; in DNA it is SH_NODE_MIX_RGB_LEGACY.
mix = nt.nodes.new("ShaderNodeMixRGB")
mix.blend_type = "MIX"      # MA_RAMP_BLEND — the only mode this bite supports.
mix.inputs["Fac"].default_value = 0.5
mix.location = (-300, 0)

# Wire color1/color2 into the mix and route the mix output into the material's
# surface input. We connect through a Principled BSDF (also in the supported
# MaterialX set already) so the material has a real surface shader.
nt.links.new(rgb_a.outputs[0], mix.inputs["Color1"])
nt.links.new(rgb_b.outputs[0], mix.inputs["Color2"])

principled = nt.nodes.new("ShaderNodeBsdfPrincipled")
principled.location = (-50, 0)
nt.links.new(mix.outputs["Color"], principled.inputs["Base Color"])
nt.links.new(principled.outputs[0], out.inputs["Surface"])

# Assign to the cube.
if cube.data.materials:
    cube.data.materials[0] = mat
else:
    cube.data.materials.append(mat)

# Export with MaterialX network enabled.
export_path = os.path.join(tempfile.gettempdir(), "test_mix_rgb_export.usda")
result = bpy.ops.wm.usd_export(
    filepath=export_path,
    export_materials=True,
    generate_materialx_network=True,
    selected_objects_only=False,
    evaluation_mode="RENDER",
)

assert "FINISHED" in result, f"USD export failed: {result}"
assert os.path.isfile(export_path), f"Export did not produce a file at {export_path}"

# Print the path on its own line for the validation step to pick up.
print(f"EXPORTED_USDA={export_path}")

# Exit cleanly so blender --background returns 0.
sys.exit(0)
