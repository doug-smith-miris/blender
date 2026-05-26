"""Regression test for BL-MAT-shadernodegroup-output-empty-prim.

Repro: a material whose Material Output's Surface socket is wired to the
output of a ShaderNodeGroup. The actual Principled BSDF lives INSIDE the
group's inner node tree (a NodeGroupOutput captures its surface). Stock
Blender's USD exporter walks `material->nodetree->all_nodes()` and the
bite-#3 connected-walker only follows `from->inputs` — neither descends
into the group's inner tree. Result: `find_bsdf_node` returns null,
`create_usd_preview_surface_material` early-returns, and the exporter emits

    def Material "MatName" { ... }

with ZERO Shader descendants. UsdPreviewSurface is missing entirely; the
surface output is unconnected; downstream renderers (Karma, Storm, etc.)
have nothing to shade with.

This is the dominant pattern on swarmfish (group-wrapped Principled across
multiple materials). The catalog fingerprint is
`BL-MAT-shadernodegroup-output-empty-prim`.

After the fix, `find_connected_bsdf` descends through ShaderNodeGroup nodes
via `find_bsdf_via_group_output` — finds the active NodeGroupOutput in the
inner tree, matches the external output socket to its internal input by
identifier, and recurses upstream. The Principled inside the group is
discovered, the UsdPreviewSurface is emitted, and its `diffuseColor` carries
the inner BSDF's Base Color.

Run with the patched Blender:

  /Users/d.smith/MirisProjects/build_darwin/bin/Blender.app/Contents/MacOS/Blender \\
      --background --python \\
      source/blender/io/usd/tests/test_miris_bl_mat_shadernodegroup_output_empty_prim.py
"""

import math
import os
import sys
import tempfile
import traceback

import bpy

INNER_BSDF_COLOR = (0.20, 0.40, 0.85)  # distinctive blue
TOL = 0.02


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def build_repro_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

    mesh = bpy.data.meshes.new("ReproMesh")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new("ReproObject", mesh)
    bpy.context.collection.objects.link(obj)

    # Build the inner shader group's node tree first.
    inner_tree = bpy.data.node_groups.new("WrappedShader", "ShaderNodeTree")
    # Expose a single "Surface" output on the group interface.
    inner_tree.interface.new_socket(name="Surface", in_out="OUTPUT", socket_type="NodeSocketShader")

    group_input = inner_tree.nodes.new("NodeGroupInput")
    group_input.location = (-400, 0)
    group_output = inner_tree.nodes.new("NodeGroupOutput")
    group_output.location = (400, 0)

    principled = inner_tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "InnerPrincipled"
    principled.location = (0, 0)
    principled.inputs["Base Color"].default_value = (*INNER_BSDF_COLOR, 1.0)

    inner_tree.links.new(principled.outputs["BSDF"], group_output.inputs["Surface"])

    # Now build the material. Use the inner group as the entire shader.
    mat = bpy.data.materials.new("ReproMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    group_node = nt.nodes.new("ShaderNodeGroup")
    group_node.name = "WrappedShaderInstance"
    group_node.node_tree = inner_tree
    group_node.location = (0, 0)

    output = nt.nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)

    nt.links.new(group_node.outputs["Surface"], output.inputs["Surface"])

    obj.data.materials.append(mat)
    return obj, mat


def main():
    obj, mat = build_repro_scene()

    tmp = tempfile.mkdtemp(prefix="bl_mat_shadernodegroup_empty_")
    out = os.path.join(tmp, "shadernodegroup_empty.usda")

    try:
        bpy.ops.wm.usd_export(
            filepath=out,
            check_existing=False,
            export_materials=True,
            export_meshes=True,
            generate_preview_surface=True,
            generate_materialx_network=False,
            evaluation_mode="RENDER",
            root_prim_path="/root",
        )
    except Exception as e:
        traceback.print_exc()
        fail(f"usd_export raised: {e}")

    print(f"USD_EXPORTED: {out}")

    try:
        from pxr import Usd, UsdShade
    except ImportError as e:
        fail(f"pxr not importable from this Blender build: {e}")

    stage = Usd.Stage.Open(out)
    if not stage:
        fail("Failed to open exported stage")

    mat_prim = None
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material) and prim.GetName() == "ReproMat":
            mat_prim = prim
            break
    if mat_prim is None:
        fail("Material 'ReproMat' not found on exported stage")

    # First, the symptom: the Material prim must have at least one Shader child.
    shader_children = [c for c in mat_prim.GetChildren() if c.IsA(UsdShade.Shader)]
    if not shader_children:
        fail(
            "Material has zero Shader descendants — BL-MAT-shadernodegroup-"
            "output-empty-prim regression. find_connected_bsdf did not descend "
            "into the ShaderNodeGroup, so the exporter emitted an empty "
            "Material prim."
        )

    usd_mat = UsdShade.Material(mat_prim)
    surface_output = usd_mat.GetSurfaceOutput()
    source_info = surface_output.GetConnectedSource()
    if not source_info:
        fail("Material surface output has no connected source")
    shader_api, source_name, _ = source_info
    shader = UsdShade.Shader(shader_api)

    shader_id = shader.GetIdAttr().Get() if shader.GetIdAttr() else ""
    if shader_id != "UsdPreviewSurface":
        fail(f"Material surface connects to shader with id={shader_id!r}, expected UsdPreviewSurface")

    diffuse_input = shader.GetInput("diffuseColor")
    if not diffuse_input:
        fail("UsdPreviewSurface has no diffuseColor input")

    value = diffuse_input.Get()
    if value is None:
        connected_src = diffuse_input.GetConnectedSource()
        fail(f"diffuseColor authored as connection (no constant): {connected_src}")

    print(f"observed_diffuseColor: {tuple(value)}")
    print(f"inner_principled_color: {INNER_BSDF_COLOR}")

    def near(a, b):
        return all(math.isclose(x, y, abs_tol=TOL) for x, y in zip(a, b))

    if not near(value, INNER_BSDF_COLOR):
        fail(
            f"Exporter wrote diffuseColor={tuple(value)}; expected the inner "
            f"Principled's Base Color {INNER_BSDF_COLOR}."
        )

    print(
        "PASS: BL-MAT-shadernodegroup-output-empty-prim — exporter descended "
        "into the ShaderNodeGroup and emitted the inner Principled's color."
    )


if __name__ == "__main__":
    main()
