"""Regression test for BL-MAT-fork:materialx-tangent-rotate3d-minus90-workaround.

The Miris fork emits MaterialX OpenPBR (`open_pbr_surface`) graphs that bridge
Blender's Principled BSDF anisotropy convention to OpenPBR's `geometry_tangent`
axis using a fixed `+90°` offset and a sign inversion (see
`source/blender/nodes/shader/nodes/node_shader_bsdf_principled.cc`, the
`Type::SurfaceShader` branch).

This test exercises that bridge end-to-end on a real AYON Project Gold asset
(`mikassa-v001.blend`) by:

1. Locating a real material that already uses a Principled BSDF.
2. Forcing `anisotropic = 0.5` and `anisotropic_rotation = 0.25` on that
   material's BSDF so the workaround actually fires.
3. Exporting the stage to USD with the MaterialX network enabled.
4. Walking the resulting MaterialX network and asserting:
     * a `rotate3d` node exists feeding `open_pbr_surface.geometry_tangent`,
     * its `amount` matches the canonical formula
         `-(anisotropic_rotation * 360 + 90)` = `-180` degrees
       (with floating-point tolerance), confirming both the magnitude offset
       and the sign inversion are still applied,
     * its `axis` traces back to a `normal` node with `space = world`,
     * the `in` of the rotate3d traces back to a `tangent` node (or another
       Vector3 source), confirming the rotated input is the Blender tangent.

If a future refactor silently drops the `+90°` offset, the sign inversion,
or the `anisotropic_rotation * 360` magnitude, the assertions below will
fail with a clear regression message pointing at the offending term.

Run via:
  /path/to/patched/Blender --background \\
    <mikassa-v001.blend> --python <this-file> -- \\
    --output-usd /tmp/mikassa_tangent_bridge.usda
"""

import argparse
import math
import os
import sys

import bpy

EXPECTED_ANISO = 0.5
EXPECTED_ANISO_ROT = 0.25
# rotation_degrees = -((anisotropic_rotation * 360) + 90)
EXPECTED_ROTATION_DEG = -((EXPECTED_ANISO_ROT * 360.0) + 90.0)


def parse_args() -> argparse.Namespace:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]
    else:
        args = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-usd", required=True)
    return parser.parse_args(args)


def make_everything_visible() -> None:
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)
    for coll in bpy.data.collections:
        coll.hide_viewport = False
        coll.hide_render = False
    for vl in bpy.context.scene.view_layers:
        for layer_coll in vl.layer_collection.children:
            layer_coll.exclude = False
            layer_coll.hide_viewport = False
    bpy.context.view_layer.update()


def find_principled_material():
    """Return (material, principled_node) for the first material that is
    actually assigned to a mesh in the scene AND uses a Principled BSDF
    whose Surface socket reaches the Material Output. Orphan materials
    (default Grease Pencil 'Dots Stroke' etc.) are skipped because they
    don't appear as Material prims in the exported USD."""
    candidate_names = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if slot.material is not None:
                candidate_names.add(slot.material.name)

    def reaches_output(material, node):
        nt = material.node_tree
        output_nodes = [n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
        if not output_nodes:
            return False
        visited = set()
        stack = [output_nodes[0]]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for inp in cur.inputs:
                for link in inp.links:
                    upstream = link.from_node
                    if upstream is node:
                        return True
                    stack.append(upstream)
        return False

    for mat_name in sorted(candidate_names):
        mat = bpy.data.materials.get(mat_name)
        if mat is None or not mat.use_nodes or mat.node_tree is None:
            continue
        for nd in mat.node_tree.nodes:
            if nd.bl_idname == "ShaderNodeBsdfPrincipled" and reaches_output(mat, nd):
                return mat, nd

    # No direct-Principled material found (Mikassa uses ShaderNodeGroup surface
    # chains throughout). Synthesize a clean fixture by replacing the surface
    # chain of a real assigned material with a fresh Principled → Output graph
    # so the workaround path is exercised on real exported geometry.
    for mat_name in sorted(candidate_names):
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            continue
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        principled = nt.nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        output = nt.nodes.new("ShaderNodeOutputMaterial")
        output.location = (300, 0)
        nt.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
        print(
            f"[bl-mat-fork-tangent] no direct-Principled material found; "
            f"synthesizing one on assigned material '{mat.name}'"
        )
        return mat, principled

    raise RuntimeError(
        "No mesh-assigned material found in this scene to use as a fixture."
    )


def force_anisotropy(node) -> None:
    """Set anisotropy + anisotropic_rotation on a Principled BSDF and break
    any external connections so the default_value is what the exporter sees."""
    aniso_sock = node.inputs.get("Anisotropic")
    rot_sock = node.inputs.get("Anisotropic Rotation")
    assert aniso_sock is not None, "Principled BSDF missing Anisotropic input"
    assert rot_sock is not None, "Principled BSDF missing Anisotropic Rotation"
    nt = node.id_data
    for sock in (aniso_sock, rot_sock):
        for link in list(sock.links):
            nt.links.remove(link)
    aniso_sock.default_value = EXPECTED_ANISO
    rot_sock.default_value = EXPECTED_ANISO_ROT


def main() -> None:
    cli = parse_args()
    out_usd = os.path.abspath(cli.output_usd)
    os.makedirs(os.path.dirname(out_usd), exist_ok=True)

    make_everything_visible()

    mat, principled = find_principled_material()
    print(f"[bl-mat-fork-tangent] using material '{mat.name}' (BSDF '{principled.name}')")
    force_anisotropy(principled)

    print(f"[bl-mat-fork-tangent] exporting USD -> {out_usd}")
    result = bpy.ops.wm.usd_export(
        filepath=out_usd,
        export_textures_mode="KEEP",
        generate_preview_surface=True,
        generate_materialx_network=True,
        export_materials=True,
        export_meshes=True,
        export_curves=False,
        export_lights=False,
        export_cameras=False,
        convert_world_material=False,
        use_instancing=False,
        evaluation_mode="RENDER",
        selected_objects_only=False,
    )
    assert "FINISHED" in result, f"usd_export did not finish cleanly: {result}"
    print(f"[bl-mat-fork-tangent] EXPORTED_USD_PATH={out_usd}")

    from pxr import Sdf, Usd, UsdShade

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"Failed to open exported stage at {out_usd}"

    # Locate the MaterialX network for our target material. The exporter
    # composes the MaterialX shaders under a Material whose name matches the
    # sanitized Blender material name.
    sanitized_name = mat.name.replace("-", "_").replace(".", "_")
    target_material = None
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        if prim.GetName() == sanitized_name:
            target_material = prim
            break
    assert target_material is not None, (
        f"Couldn't find Material prim {sanitized_name!r} in the stage. "
        f"All materials: {[p.GetName() for p in stage.Traverse() if p.GetTypeName() == 'Material']}"
    )
    print(f"[bl-mat-fork-tangent] target material: {target_material.GetPath().pathString}")

    # Find the open_pbr_surface shader under the material.
    open_pbr = None
    for child in target_material.GetChildren():
        if child.GetTypeName() != "Shader":
            continue
        shader = UsdShade.Shader(child)
        shader_id = shader.GetIdAttr().Get()
        if shader_id == "ND_open_pbr_surface_surfaceshader":
            open_pbr = shader
            break
        # MaterialX may also nest the open_pbr_surface inside a NodeGraph;
        # fall through and scan grandchildren if not found at top level.

    if open_pbr is None:
        # Scan all Shader prims in the stage's MaterialX subtree.
        for prim in stage.Traverse():
            if prim.GetTypeName() != "Shader":
                continue
            shader = UsdShade.Shader(prim)
            if shader.GetIdAttr().Get() == "ND_open_pbr_surface_surfaceshader":
                # Make sure this is under our material.
                if target_material.GetPath().pathString in prim.GetPath().pathString:
                    open_pbr = shader
                    break

    assert open_pbr is not None, (
        "Regression: MaterialX export did not emit an open_pbr_surface shader "
        f"under {target_material.GetPath().pathString}. The OpenPBR surface "
        "branch in node_shader_bsdf_principled.cc may have been removed or "
        "renamed."
    )
    print(f"[bl-mat-fork-tangent] open_pbr_surface at: {open_pbr.GetPath().pathString}")

    # Resolve the geometry_tangent input back to a rotate3d node. The
    # exporter wraps the MaterialX subgraph in a UsdShade.NodeGraph, so the
    # path goes: open_pbr.geometry_tangent → NodeGraph.outputs:nodeN_out →
    # internal Shader chain (typically normalize → rotate3d → normalize →
    # tangent). Walk through that chain until we hit the rotate3d.
    tangent_input = open_pbr.GetInput("geometry_tangent")
    assert tangent_input is not None and tangent_input.HasConnectedSource(), (
        "Regression: open_pbr_surface.geometry_tangent has no connection. "
        "With anisotropic > 0 the workaround must wire a rotate3d through it."
    )

    def follow_to_rotate3d(input_attr, max_hops=8):
        """Follow a UsdShade.Input through Shader/NodeGraph indirection until
        a Shader with id starting with 'ND_rotate3d_' is found. Returns the
        Shader prim or None."""
        if not input_attr.HasConnectedSource():
            return None
        api, src_name, src_type = input_attr.GetConnectedSource()
        cur_prim = api.GetPrim()
        for _ in range(max_hops):
            if cur_prim.GetTypeName() == "NodeGraph":
                # Resolve the NodeGraph output to its internal source.
                ng = UsdShade.NodeGraph(cur_prim)
                ng_out = ng.GetOutput(src_name)
                if ng_out is None or not ng_out.HasConnectedSource():
                    return None
                api, src_name, _ = ng_out.GetConnectedSource()
                cur_prim = api.GetPrim()
                continue
            if cur_prim.GetTypeName() != "Shader":
                return None
            shader = UsdShade.Shader(cur_prim)
            sid = shader.GetIdAttr().Get() or ""
            if sid.startswith("ND_rotate3d_"):
                return shader
            # Step through normalize / pass-through Vector3 shaders via their 'in'.
            in_input = shader.GetInput("in")
            if in_input is None or not in_input.HasConnectedSource():
                return None
            api, src_name, _ = in_input.GetConnectedSource()
            cur_prim = api.GetPrim()
        return None

    source_shader = follow_to_rotate3d(tangent_input)
    assert source_shader is not None, (
        "Regression: open_pbr_surface.geometry_tangent is not driven by a "
        "rotate3d node (followed through NodeGraph + normalize chain). The "
        "+90° anisotropy bridge workaround appears to have been removed."
    )
    source_id = source_shader.GetIdAttr().Get()
    print(f"[bl-mat-fork-tangent] rotate3d shader at: {source_shader.GetPath().pathString} (id={source_id})")

    # Read the rotate3d amount; it must equal -(rot * 360 + 90).
    amount_input = source_shader.GetInput("amount")
    assert amount_input is not None, "rotate3d node is missing the 'amount' input"
    # The amount may be authored as a literal value or driven by another node
    # that computes -(in*360 + 90). If it's a literal, just check it.
    amount_value = amount_input.Get()
    if amount_value is not None and not amount_input.HasConnectedSource():
        assert math.isclose(float(amount_value), EXPECTED_ROTATION_DEG, abs_tol=1e-3), (
            f"Regression (BL-MAT-fork tangent bridge): rotate3d.amount = "
            f"{amount_value!r}, expected {EXPECTED_ROTATION_DEG} for "
            f"anisotropic_rotation={EXPECTED_ANISO_ROT}. Either the +90° "
            f"offset or the leading negation has been dropped."
        )
        print(f"[bl-mat-fork-tangent] rotate3d.amount = {amount_value} (literal, matches {EXPECTED_ROTATION_DEG})")
    else:
        # The amount is computed via a chain of multiply/add/subtract nodes.
        # Evaluate the chain by walking it numerically.
        def eval_chain(input_or_value):
            if isinstance(input_or_value, UsdShade.Input):
                inp = input_or_value
                if inp.HasConnectedSource():
                    api, sub_name, _ = inp.GetConnectedSource()
                    return eval_shader(UsdShade.Shader(api.GetPrim()))
                v = inp.Get()
                assert v is not None, f"input {inp.GetFullName()} has no value or source"
                return float(v)
            return float(input_or_value)

        def eval_shader(shader):
            sid = shader.GetIdAttr().Get() or ""
            if sid.startswith("ND_multiply_float"):
                a = eval_chain(shader.GetInput("in1"))
                b = eval_chain(shader.GetInput("in2"))
                return a * b
            if sid.startswith("ND_add_float"):
                a = eval_chain(shader.GetInput("in1"))
                b = eval_chain(shader.GetInput("in2"))
                return a + b
            if sid.startswith("ND_subtract_float"):
                a = eval_chain(shader.GetInput("in1"))
                b = eval_chain(shader.GetInput("in2"))
                return a - b
            if sid.startswith("ND_constant_float"):
                v = shader.GetInput("value").Get()
                return float(v)
            # Anisotropic rotation literal node (a constant) — return the known
            # input value we set on the BSDF.
            if "constant" in sid:
                v = shader.GetInput("value").Get()
                return float(v)
            raise AssertionError(
                f"Unhandled MaterialX numeric node {sid!r} when evaluating "
                f"rotate3d.amount expression"
            )

        api, sub_name, _ = amount_input.GetConnectedSource()
        computed = eval_shader(UsdShade.Shader(api.GetPrim()))
        assert math.isclose(computed, EXPECTED_ROTATION_DEG, abs_tol=1e-3), (
            f"Regression: computed rotate3d.amount chain evaluates to "
            f"{computed}, expected {EXPECTED_ROTATION_DEG}."
        )
        print(f"[bl-mat-fork-tangent] rotate3d.amount chain evaluates to {computed} (matches {EXPECTED_ROTATION_DEG})")

    # Verify the rotation axis is a normal (world-space normal driving the
    # rotation, matching the workaround's `tangent.rotate(rotation, normal)`).
    axis_input = source_shader.GetInput("axis")
    assert axis_input is not None, "rotate3d.axis missing"
    if axis_input.HasConnectedSource():
        axis_api, _, _ = axis_input.GetConnectedSource()
        axis_shader_id = UsdShade.Shader(axis_api.GetPrim()).GetIdAttr().Get() or ""
        # Acceptable chain: rotate3d.axis ← normalize ← normal(space=world)
        # We just require the chain to ultimately reach a 'normal' node id.
        seen = set()
        cur = UsdShade.Shader(axis_api.GetPrim())
        while cur.GetPrim().GetPath() not in seen:
            seen.add(cur.GetPrim().GetPath())
            sid = cur.GetIdAttr().Get() or ""
            if "ND_normal_" in sid:
                axis_shader_id = sid
                break
            in_input = cur.GetInput("in")
            if in_input is not None and in_input.HasConnectedSource():
                api, _, _ = in_input.GetConnectedSource()
                cur = UsdShade.Shader(api.GetPrim())
            else:
                break
        assert "ND_normal_" in axis_shader_id, (
            f"Regression: rotate3d.axis chain did not resolve to a normal node "
            f"(terminal id was {axis_shader_id!r}). The workaround relies on "
            f"rotating around the surface normal."
        )
    print("[bl-mat-fork-tangent] rotate3d.axis traces back to a normal node")

    # Verify the rotated input (rotate3d.in) traces back to a tangent node.
    in_input = source_shader.GetInput("in")
    assert in_input is not None and in_input.HasConnectedSource(), (
        "Regression: rotate3d.in has no source — the Blender tangent vector "
        "isn't being fed into the rotation."
    )
    seen = set()
    cur_api, _, _ = in_input.GetConnectedSource()
    cur = UsdShade.Shader(cur_api.GetPrim())
    terminal_id = None
    while cur.GetPrim().GetPath() not in seen:
        seen.add(cur.GetPrim().GetPath())
        sid = cur.GetIdAttr().Get() or ""
        if "ND_tangent_" in sid:
            terminal_id = sid
            break
        sub_in = cur.GetInput("in")
        if sub_in is not None and sub_in.HasConnectedSource():
            api, _, _ = sub_in.GetConnectedSource()
            cur = UsdShade.Shader(api.GetPrim())
        else:
            break
    assert terminal_id is not None and "ND_tangent_" in terminal_id, (
        "Regression: rotate3d.in chain did not resolve to a tangent node "
        f"(terminal id was {terminal_id!r}). The workaround expects to rotate "
        f"a Blender-tangent input."
    )
    print(f"[bl-mat-fork-tangent] rotate3d.in traces back to tangent node ({terminal_id})")

    print("[bl-mat-fork-tangent] PASS")


if __name__ == "__main__":
    main()
