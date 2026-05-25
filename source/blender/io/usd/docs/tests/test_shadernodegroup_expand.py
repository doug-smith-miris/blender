# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Round-trip test for the USD exporter's expansion of ShaderNodeGroup wrappers
(finding BL-MAT-NG-001-shadernodegroup-not-expanded).

Reproduces the failure mode observed on swarmfish.blend's `creature-eyes` and
`creature-pupil` materials: the Output Material's Surface input is fed by a
single ShaderNodeGroup whose internal tree wraps a Principled BSDF. Before the
fix, `usd_writer_material.cc::find_bsdf_node` only iterated the top-level node
tree (`material->nodetree->all_nodes()`), so the BSDF inside the group was
invisible and the exporter emitted an empty Material prim with no shader
network at all. After the fix, `find_bsdf_node` recurses into nested
ShaderNodeGroups and a UsdPreviewSurface shader is emitted.

The graph constructed here mirrors the swarmfish creature-eye structure
(per pipeline-runs/edefbd0e-499d-4d13-b3e0-1d2418b96960/materials.json:6151):
the material at top level contains only Output Material + ShaderNodeGroup, and
the group node tree contains a Principled BSDF feeding the group's Shader
output.

Invocation:

    /Applications/Blender.app/Contents/MacOS/Blender \\
        --background \\
        --python source/blender/io/usd/docs/tests/test_shadernodegroup_expand.py

Prints ``EXPORTED_USDA=<path>`` on its own line for the validation step
(``validate_shadernodegroup_expand.py``) to pick up.
"""

import os
import sys
import tempfile

import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object
assert cube is not None, "Cube creation failed"

# Build the inner node tree that the group will wrap. To keep the test focused
# on `find_bsdf_node`'s recursion (the BL-MAT-NG-001 fix), the inner BSDF's
# inputs are set as socket defaults rather than driven by upstream nodes —
# tracing through arbitrary upstream nodes (ShaderNodeRGB, ShaderNodeValue,
# Color Ramp, etc.) is a separate finding (BL-MAT-001) and the existing
# traverse_channel doesn't walk through value-shaping nodes anyway. With
# socket defaults, the exporter's "no upstream image/attribute → write
# default constant" path emits known values into the UsdPreviewSurface.
inner_tree = bpy.data.node_groups.new(name="SH-test-eye", type="ShaderNodeTree")

# Group I/O interfaces. Even though this group has no outside connections in
# the parent material (matching the swarmfish creature-eyes layout), the
# interface still must declare a Shader output so the group node has an
# output socket to wire from.
inner_tree.interface.new_socket(
    name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader"
)

# Inner nodes.
inner_in = inner_tree.nodes.new("NodeGroupInput")
inner_in.location = (-700, 0)
inner_out = inner_tree.nodes.new("NodeGroupOutput")
inner_out.location = (400, 0)

inner_principled = inner_tree.nodes.new("ShaderNodeBsdfPrincipled")
inner_principled.location = (100, 0)

# Set BSDF socket defaults to known values. The exporter falls through to
# these when no upstream image/attribute exists.
inner_principled.inputs["Base Color"].default_value = (0.85, 0.20, 0.30, 1.0)
inner_principled.inputs["Roughness"].default_value = 0.42
inner_principled.inputs["Metallic"].default_value = 0.0

# Wire the BSDF into the group's Shader output.
inner_tree.links.new(inner_principled.outputs[0], inner_out.inputs["Shader"])

# Now the outer material: Output Material <- ShaderNodeGroup. No Principled BSDF
# at the top level; the only thing reaching Surface is the group node. This is
# exactly the structure that produces an empty Material prim before the fix.
mat = bpy.data.materials.new(name="GroupWrappedMaterial")
mat.use_nodes = True
nt = mat.node_tree

# Strip the default Principled BSDF.
for node in list(nt.nodes):
    if node.type != "OUTPUT_MATERIAL":
        nt.nodes.remove(node)

out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")

group_node = nt.nodes.new("ShaderNodeGroup")
group_node.node_tree = inner_tree
group_node.location = (-200, 0)

nt.links.new(group_node.outputs["Shader"], out.inputs["Surface"])

# Assign to the cube.
if cube.data.materials:
    cube.data.materials[0] = mat
else:
    cube.data.materials.append(mat)

# Export with the default UsdPreviewSurface path (NOT MaterialX). This is the
# path that contains `find_bsdf_node` and `traverse_channel`.
export_path = os.path.join(tempfile.gettempdir(), "test_shadernodegroup_expand.usda")
result = bpy.ops.wm.usd_export(
    filepath=export_path,
    export_materials=True,
    generate_materialx_network=False,
    selected_objects_only=False,
    evaluation_mode="RENDER",
)

assert "FINISHED" in result, f"USD export failed: {result}"
assert os.path.isfile(export_path), f"Export did not produce a file at {export_path}"

print(f"EXPORTED_USDA={export_path}")
sys.exit(0)
