"""Regression test for BL-MAT-fork-anisotropy-axis-visual-mismatch-tune-pr9.

PR #9 documented the OpenPBR anisotropy bridge magic numbers but did not
change the math. The resulting Karma highlight was still in the wrong
direction AND wrong stretch magnitude vs the Cycles reference (see
arch_run 70582d9d).

This bite derives the correct formula from first principles:

 1. open_pbr_surface routes `geometry_tangent` directly to its internal
    `dielectric_bsdf.tangent` (and `generalized_schlick_bsdf.tangent`),
    the same dielectric_bsdf the lower-level Type::BSDF branch builds
    explicitly. So the tangent rotation must match the Type::BSDF branch:
        rotation_deg = anisotropic_rotation * 360
    (No +90° offset, no leading negation.)

 2. Magnitude scale comes from matching the alpha_x/alpha_y ratios:
        Cycles : ratio = 1 / (1 - A * 0.9)         (closure.h:157-164)
        OpenPBR: ratio = 1 / (1 - A')              (NG_open_pbr_anisotropy)
    Setting the two equal gives A' = A * 0.9. This is the derived
    replacement for the previous empirical 0.7 fudge factor.

This test exercises the bridge end-to-end on a real AYON Project Gold asset
(`mikassa-v001.blend`) by:

 1. Locating a real material that already uses a Principled BSDF.
 2. Forcing `anisotropic = 0.5` and `anisotropic_rotation = 0.25` on that
    material's BSDF so the bridge path actually fires.
 3. Exporting the stage to USD with the MaterialX network enabled.
 4. Walking the resulting MaterialX network and asserting:
      * a `rotate3d` node exists feeding `open_pbr_surface.geometry_tangent`,
      * its `amount` chain evaluates to `anisotropic_rotation * 360`
        (= 90.0 for rot=0.25), NOT to `-(rot*360 + 90)` (= -180.0).
      * `open_pbr_surface.specular_roughness_anisotropy` chain evaluates
        to `anisotropic * 0.9` (= 0.45 for aniso=0.5), NOT to
        `anisotropic * 0.7` (= 0.35).

A regression that re-introduces the +90° offset, the leading negation, or
restores the 0.7 magnitude scale will fail with a clear assertion message
naming the offending term.

Run via:
  /path/to/patched/Blender --background \
    <mikassa-v001.blend> --python <this-file> -- \
    --output-usd /tmp/mikassa_anisotropy_tune.usda
"""

import argparse
import math
import os
import sys

import bpy

EXPECTED_ANISO = 0.5
EXPECTED_ANISO_ROT = 0.25

CYCLES_TO_OPENPBR_ANISOTROPY_SCALE = 0.9
ANISOTROPIC_ROTATION_TO_DEG = 360.0

EXPECTED_ROTATION_DEG = EXPECTED_ANISO_ROT * ANISOTROPIC_ROTATION_TO_DEG
EXPECTED_ANISO_SCALED = EXPECTED_ANISO * CYCLES_TO_OPENPBR_ANISOTROPY_SCALE

LEGACY_ROTATION_DEG = -((EXPECTED_ANISO_ROT * 360.0) + 90.0)
LEGACY_ANISO_SCALED = EXPECTED_ANISO * 0.7


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
    """Return (material, principled_node). Prefer a Principled BSDF whose
    Surface socket reaches the Material Output of a mesh-assigned material.
    Fall back to synthesizing a clean fixture on an assigned material."""
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
            f"[bl-mat-fork-aniso-tune] no direct-Principled material found; "
            f"synthesizing one on assigned material '{mat.name}'"
        )
        return mat, principled

    raise RuntimeError(
        "No mesh-assigned material found in this scene to use as a fixture."
    )


def force_anisotropy(node) -> None:
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


def eval_numeric_chain(input_attr, max_depth=32):
    """Walk a UsdShade.Input through ND_multiply/add/subtract/divide/constant
    nodes (and inline literals) and return a float. Raises if it hits an
    unknown shader id."""
    from pxr import UsdShade

    visited = set()

    def eval_shader(shader, depth):
        if depth > max_depth:
            raise AssertionError("Recursion limit while evaluating numeric chain")
        path = shader.GetPrim().GetPath().pathString
        if path in visited:
            raise AssertionError(f"Numeric-chain cycle at {path}")
        visited.add(path)
        sid = shader.GetIdAttr().Get() or ""

        def get_op_input(name):
            inp = shader.GetInput(name)
            assert inp is not None, f"{sid} missing input {name!r}"
            return eval_input(inp, depth + 1)

        if sid.startswith("ND_multiply_float"):
            return get_op_input("in1") * get_op_input("in2")
        if sid.startswith("ND_add_float"):
            return get_op_input("in1") + get_op_input("in2")
        if sid.startswith("ND_subtract_float"):
            return get_op_input("in1") - get_op_input("in2")
        if sid.startswith("ND_divide_float"):
            return get_op_input("in1") / get_op_input("in2")
        if sid.startswith("ND_constant_float"):
            v = shader.GetInput("value").Get()
            return float(v)
        # The exporter sometimes wraps a constant as a single-input passthrough.
        in_input = shader.GetInput("in")
        if in_input is not None:
            return eval_input(in_input, depth + 1)
        v_input = shader.GetInput("value")
        if v_input is not None:
            v = v_input.Get()
            return float(v)
        raise AssertionError(
            f"Unhandled numeric shader {sid!r} at {path} when evaluating chain"
        )

    def eval_input(inp, depth):
        if inp.HasConnectedSource():
            api, _, _ = inp.GetConnectedSource()
            prim = api.GetPrim()
            if prim.GetTypeName() == "NodeGraph":
                # Resolve through the NodeGraph output
                ng = UsdShade.NodeGraph(prim)
                _, src_name, _ = inp.GetConnectedSource()
                ng_out = ng.GetOutput(src_name)
                if ng_out is not None and ng_out.HasConnectedSource():
                    api2, _, _ = ng_out.GetConnectedSource()
                    return eval_shader(UsdShade.Shader(api2.GetPrim()), depth + 1)
                v = ng_out.Get() if ng_out is not None else None
                if v is None:
                    raise AssertionError(f"NodeGraph output {src_name} unresolvable")
                return float(v)
            return eval_shader(UsdShade.Shader(prim), depth + 1)
        v = inp.Get()
        assert v is not None, f"input {inp.GetFullName()} has no value or source"
        return float(v)

    return eval_input(input_attr, 0)


def main() -> None:
    cli = parse_args()
    out_usd = os.path.abspath(cli.output_usd)
    os.makedirs(os.path.dirname(out_usd), exist_ok=True)

    make_everything_visible()

    mat, principled = find_principled_material()
    print(f"[bl-mat-fork-aniso-tune] using material '{mat.name}' (BSDF '{principled.name}')")
    force_anisotropy(principled)

    print(f"[bl-mat-fork-aniso-tune] exporting USD -> {out_usd}")
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
    print(f"[bl-mat-fork-aniso-tune] EXPORTED_USD_PATH={out_usd}")

    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(out_usd)
    assert stage, f"Failed to open exported stage at {out_usd}"

    sanitized_name = mat.name.replace("-", "_").replace(".", "_")
    target_material = None
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        if prim.GetName() == sanitized_name:
            target_material = prim
            break
    assert target_material is not None, (
        f"Couldn't find Material prim {sanitized_name!r}. All materials: "
        f"{[p.GetName() for p in stage.Traverse() if p.GetTypeName() == 'Material']}"
    )
    print(f"[bl-mat-fork-aniso-tune] target material: {target_material.GetPath().pathString}")

    open_pbr = None
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader":
            continue
        shader = UsdShade.Shader(prim)
        if shader.GetIdAttr().Get() == "ND_open_pbr_surface_surfaceshader":
            if target_material.GetPath().pathString in prim.GetPath().pathString:
                open_pbr = shader
                break

    assert open_pbr is not None, (
        "Regression: MaterialX export did not emit an open_pbr_surface shader "
        f"under {target_material.GetPath().pathString}."
    )
    print(f"[bl-mat-fork-aniso-tune] open_pbr_surface at: {open_pbr.GetPath().pathString}")

    # --- Assertion 1: rotation_deg = aniso_rot * 360  (no offset, no negation) ---

    tangent_input = open_pbr.GetInput("geometry_tangent")
    assert tangent_input is not None and tangent_input.HasConnectedSource(), (
        "Regression: open_pbr_surface.geometry_tangent has no connection. "
        "With anisotropic > 0 a rotate3d must be wired through it."
    )

    def follow_to_rotate3d(input_attr, max_hops=12):
        if not input_attr.HasConnectedSource():
            return None
        api, src_name, _ = input_attr.GetConnectedSource()
        cur_prim = api.GetPrim()
        for _ in range(max_hops):
            if cur_prim.GetTypeName() == "NodeGraph":
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
            in_input = shader.GetInput("in")
            if in_input is None or not in_input.HasConnectedSource():
                return None
            api, src_name, _ = in_input.GetConnectedSource()
            cur_prim = api.GetPrim()
        return None

    rotate_shader = follow_to_rotate3d(tangent_input)
    assert rotate_shader is not None, (
        "Regression: open_pbr_surface.geometry_tangent does not trace back to a "
        "rotate3d node."
    )
    print(f"[bl-mat-fork-aniso-tune] rotate3d at: {rotate_shader.GetPath().pathString}")

    amount_input = rotate_shader.GetInput("amount")
    assert amount_input is not None, "rotate3d node is missing the 'amount' input"

    if amount_input.HasConnectedSource():
        amount_value = eval_numeric_chain(amount_input)
        amount_origin = "computed chain"
    else:
        raw = amount_input.Get()
        amount_value = float(raw) if raw is not None else None
        amount_origin = "literal"

    assert amount_value is not None, "rotate3d.amount unresolvable"
    print(
        f"[bl-mat-fork-aniso-tune] rotate3d.amount = {amount_value} "
        f"({amount_origin}; expected {EXPECTED_ROTATION_DEG})"
    )
    assert math.isclose(amount_value, EXPECTED_ROTATION_DEG, abs_tol=1e-3), (
        f"Regression (BL-MAT-fork-aniso-tune-pr9): rotate3d.amount = "
        f"{amount_value}, expected {EXPECTED_ROTATION_DEG} (= "
        f"anisotropic_rotation * 360 with no offset, no negation). Did "
        f"someone re-introduce the +90° offset or the leading negation? "
        f"Legacy (pre-fix) value would have been {LEGACY_ROTATION_DEG}."
    )

    # --- Assertion 2: anisotropy_scaled = anisotropic * 0.9 (not 0.7) ---

    aniso_input = open_pbr.GetInput("specular_roughness_anisotropy")
    assert aniso_input is not None, (
        "open_pbr_surface missing specular_roughness_anisotropy input"
    )

    if aniso_input.HasConnectedSource():
        # Resolve through NodeGraph if needed.
        api, src_name, _ = aniso_input.GetConnectedSource()
        prim = api.GetPrim()
        if prim.GetTypeName() == "NodeGraph":
            ng = UsdShade.NodeGraph(prim)
            ng_out = ng.GetOutput(src_name)
            assert ng_out is not None and ng_out.HasConnectedSource(), (
                f"NodeGraph output {src_name} for anisotropy has no source"
            )
            aniso_value = eval_numeric_chain(ng_out)
        else:
            aniso_value = eval_numeric_chain(aniso_input)
        aniso_origin = "computed chain"
    else:
        raw = aniso_input.Get()
        aniso_value = float(raw) if raw is not None else None
        aniso_origin = "literal"

    assert aniso_value is not None, "specular_roughness_anisotropy unresolvable"
    print(
        f"[bl-mat-fork-aniso-tune] specular_roughness_anisotropy = {aniso_value} "
        f"({aniso_origin}; expected {EXPECTED_ANISO_SCALED})"
    )
    assert math.isclose(aniso_value, EXPECTED_ANISO_SCALED, abs_tol=1e-3), (
        f"Regression (BL-MAT-fork-aniso-tune-pr9): "
        f"specular_roughness_anisotropy = {aniso_value}, expected "
        f"{EXPECTED_ANISO_SCALED} (= anisotropic * 0.9, the derived "
        f"Cycles→OpenPBR aspect-ratio match). Legacy (pre-fix) 0.7 scale "
        f"would have produced {LEGACY_ANISO_SCALED}."
    )

    print("[bl-mat-fork-aniso-tune] PASS")


if __name__ == "__main__":
    main()
