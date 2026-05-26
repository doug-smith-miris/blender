"""Regression test for BL-MAT-ghost-principled-misattributed-from-disconnected-node.

Repro: a material whose Material Output's Surface socket is wired to a Mix
Shader that mixes the *real* Principled BSDF with a Transparent BSDF, plus a
second "ghost" Principled BSDF that the artist left disconnected in the graph
(common after iteration). The stock exporter walks `node_tree.all_nodes()` and
returns the first Principled it finds — which is the ghost — and writes its
default colour into the exported UsdPreviewSurface. After the fix, the
exporter walks backward from `Output Material.Surface` and finds the actually-
connected Principled.

We mark the two Principled nodes with distinctive Base Colors:

  connected ghost (right one): rgb(0.10, 0.70, 0.20)   # greenish
  disconnected ghost:          rgb(0.95, 0.05, 0.05)   # red

A correct exporter writes ~(0.10, 0.70, 0.20) into the UsdPreviewSurface's
`diffuseColor`. A broken exporter writes ~(0.95, 0.05, 0.05).

Run with the patched Blender binary:

  /Users/d.smith/MirisProjects/build_darwin/bin/Blender.app/Contents/MacOS/Blender \\
      --background --python \\
      source/blender/io/usd/tests/test_miris_bl_mat_ghost_principled_misattributed_from_disconnected_node.py
"""

import math
import os
import sys
import tempfile
import traceback

import bpy

CONNECTED_COLOR = (0.10, 0.70, 0.20)
GHOST_COLOR = (0.95, 0.05, 0.05)
TOL = 0.01


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

    mat = bpy.data.materials.new("ReproMat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    # Create the GHOST Principled first so it appears earlier in
    # `node_tree.all_nodes()` iteration order — that is what exposes the
    # original first-match bug. The legacy exporter walks all_nodes() linearly
    # and grabs whichever Principled comes first, irrespective of whether it
    # is wired to the Material Output's Surface socket.
    ghost = nt.nodes.new("ShaderNodeBsdfPrincipled")
    ghost.name = "Ghost_Principled"
    ghost.location = (-200, -500)
    ghost.inputs["Base Color"].default_value = (*GHOST_COLOR, 1.0)

    connected = nt.nodes.new("ShaderNodeBsdfPrincipled")
    connected.name = "Connected_Principled"
    connected.location = (-200, 100)
    connected.inputs["Base Color"].default_value = (*CONNECTED_COLOR, 1.0)

    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200, -200)

    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (200, 0)
    mix.inputs[0].default_value = 0.0  # fully favour the Principled

    output = nt.nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)

    nt.links.new(connected.outputs["BSDF"], mix.inputs[1])
    nt.links.new(transparent.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], output.inputs["Surface"])

    obj.data.materials.append(mat)
    return obj, mat


def main():
    obj, mat = build_repro_scene()

    tmp = tempfile.mkdtemp(prefix="bl_mat_ghost_principled_")
    out = os.path.join(tmp, "ghost_principled.usda")

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

    usd_mat = UsdShade.Material(mat_prim)
    surface_output = usd_mat.GetSurfaceOutput()
    source_info = surface_output.GetConnectedSource()
    if not source_info:
        fail("Material has no surface output connection")
    shader_api, source_name, _ = source_info
    shader = UsdShade.Shader(shader_api)
    diffuse_input = shader.GetInput("diffuseColor")
    if not diffuse_input:
        fail("UsdPreviewSurface has no diffuseColor input")

    value = diffuse_input.Get()
    if value is None:
        connected_src = diffuse_input.GetConnectedSource()
        fail(f"diffuseColor authored as connection (no constant): {connected_src}")

    print(f"observed_diffuseColor: {tuple(value)}")
    print(f"connected_principled_color:    {CONNECTED_COLOR}")
    print(f"ghost_principled_color:        {GHOST_COLOR}")

    def near(a, b):
        return all(math.isclose(x, y, abs_tol=TOL) for x, y in zip(a, b))

    if near(value, GHOST_COLOR):
        fail(
            "Exporter wrote the GHOST (disconnected) Principled's color into "
            "UsdPreviewSurface.diffuseColor. BL-MAT-ghost-principled-"
            "misattributed-from-disconnected-node regression — find_bsdf_node "
            "is still scanning all_nodes() instead of walking back from the "
            "active Output Material's Surface socket."
        )
    if not near(value, CONNECTED_COLOR):
        fail(
            f"Exporter wrote an unexpected color {tuple(value)}; expected the "
            f"connected Principled's color {CONNECTED_COLOR}."
        )

    print("PASS: BL-MAT-ghost-principled-misattributed-from-disconnected-node — exporter chose the connected Principled.")


if __name__ == "__main__":
    main()
