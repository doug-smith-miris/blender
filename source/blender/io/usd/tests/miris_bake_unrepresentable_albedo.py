"""
Miris USD exporter post-processor: bake-unrepresentable-albedo.

When a Blender material's Principled BSDF Base Color is reachable only
through nodes that UsdPreviewSurface cannot losslessly represent
(HSV, RGB Curves, Mix, Vector Math, Color Ramp, ...), the new
allowlist-gated traversal in usd_writer_material.cc:traverse_channel
correctly refuses to wire any of them and falls back to the BSDF's
literal default color -- which on a painterly material (swarmfish
creature_body) is far from the artist's intent. This module closes
that gap by:

  1. Detecting materials whose Base Color upstream chain contains a
     node outside the exporter's signal-carrier allowlist.
  2. Baking each such material's diffuse color (Cycles, type='DIFFUSE',
     pass_filter={'COLOR'}, direct=False, indirect=False) to a PNG
     written next to the exported .usd.
  3. Editing the exported USD to point the UsdPreviewSurface
     diffuseColor at a new UsdUVTexture pointing at the baked PNG,
     wired through a UsdPrimvarReader_float2 for the geometry's
     authored UV set (default "st").

Driving entry point: bake_and_export(filepath, **wm_usd_export_kwargs).
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

import bpy

# --- Allowlist (mirrors usd_writer_material.cc::is_signal_carrying_input) ----
# Node types we are willing to walk through when looking upstream for a
# representable signal (e.g. an Image Texture). Sockets are matched by
# the Blender display name.
_SIGNAL_CARRIER_INPUTS = {
    "NodeReroute":           lambda s: True,
    "ShaderNodeNormalMap":   lambda s: s == "Color",
    "ShaderNodeSeparateColor": lambda s: s == "Color",
    "ShaderNodeBump":        lambda s: s == "Height",
    "ShaderNodeDisplacement": lambda s: s == "Height",
    "ShaderNodeCombineColor": lambda s: s in ("Red", "Green", "Blue"),
    "ShaderNodeMapping":     lambda s: s == "Vector",
    "ShaderNodeMath":        lambda s: s == "Value",
    "ShaderNodeVectorMath":  lambda s: s == "Vector",
}


def _is_signal_carrier(node: bpy.types.Node, socket_name: str) -> bool:
    pred = _SIGNAL_CARRIER_INPUTS.get(node.bl_idname)
    return bool(pred and pred(socket_name))


def _find_principled_recursive(tree: "bpy.types.NodeTree") -> Optional["bpy.types.Node"]:
    """Find a Principled BSDF anywhere in `tree`, including inside nested
    ShaderNodeGroup sub-trees. Mirrors the C++ find_node_of_type_recursive
    helper in usd_writer_material.cc that PR #18 added so empty Material
    prims don't get emitted when the BSDF is wrapped in a group."""
    if tree is None:
        return None
    for n in tree.nodes:
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            return n
    for n in tree.nodes:
        if n.bl_idname == "ShaderNodeGroup" and getattr(n, "node_tree", None) is not None:
            found = _find_principled_recursive(n.node_tree)
            if found is not None:
                return found
    return None


def _outer_surface_has_lightpath_cutout(material: bpy.types.Material) -> bool:
    """Walk back from the active Material Output's Surface socket and
    return True if a Mix Shader fed by a Light Path / Transparent BSDF
    sits between the output and the inner BSDF — that's body_mouth_bag's
    visibility cutout, which Cycles renders but UsdPreviewSurface cannot
    losslessly express, so the diffuse is best captured via a bake."""
    if not material or not material.use_nodes or not material.node_tree:
        return False
    out = None
    for n in material.node_tree.nodes:
        if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output:
            out = n
            break
    if out is None:
        for n in material.node_tree.nodes:
            if n.bl_idname == "ShaderNodeOutputMaterial":
                out = n
                break
    if out is None:
        return False
    surf = out.inputs.get("Surface")
    if surf is None or not surf.is_linked:
        return False

    visited: set["bpy.types.Node"] = set()

    def walk(socket: "bpy.types.NodeSocket", depth: int = 0) -> bool:
        if depth > 8 or not socket.is_linked:
            return False
        node = socket.links[0].from_node
        if node in visited:
            return False
        visited.add(node)
        bl = node.bl_idname
        if bl == "ShaderNodeMixShader":
            # Light-Path-driven Mix Shader = cutout pattern.
            fac = node.inputs.get("Fac") or (node.inputs[0] if node.inputs else None)
            if fac is not None and fac.is_linked:
                src = fac.links[0].from_node
                if src.bl_idname == "ShaderNodeLightPath":
                    return True
            # Otherwise keep descending into both shader inputs.
        if bl == "NodeReroute":
            return walk(node.inputs[0], depth + 1)
        for inp in node.inputs:
            if inp.type == "SHADER" and walk(inp, depth + 1):
                return True
        return False

    return walk(surf)


def _base_color_chain_is_unrepresentable(material: bpy.types.Material) -> bool:
    """True if the Principled BSDF Base Color is linked through a node
    that the allowlist refuses to descend through, OR if the outer
    surface network wraps the BSDF in a Light-Path/Transparent-BSDF Mix
    Shader cutout (body_mouth_bag). False if Base Color is unlinked, or
    linked directly to a representable terminal (e.g. Image Texture).
    Recurses across signal-carrier nodes AND across ShaderNodeGroup
    boundaries (group input/output)."""
    if not material or not material.use_nodes or not material.node_tree:
        return False

    # Recurse into nested ShaderNodeGroups to find the Principled BSDF.
    bsdf = _find_principled_recursive(material.node_tree)
    if bsdf is None:
        return False

    base = bsdf.inputs.get("Base Color")
    if base is None:
        return False

    # If the outer-material surface wraps everything in a Light-Path
    # cutout (body_mouth_bag-style) we want to bake regardless of how
    # the inner Base Color is wired -- Cycles renders the cutout as
    # visible-where-camera and the PreviewSurface fallback can't.
    if _outer_surface_has_lightpath_cutout(material):
        return True

    if not base.is_linked:
        return False

    # `group_stack` lets the walk ascend back through GROUP_INPUT to the
    # caller's outer socket, mirroring traverse_channel in PR #10/#15.
    visited: set[bpy.types.Node] = set()

    def walk(socket: bpy.types.NodeSocket, group_stack: list) -> bool:
        # Returns True if upstream chain hits an unrepresentable modifier.
        if not socket.is_linked:
            return False
        link = socket.links[0]
        upstream = link.from_node
        from_sock = link.from_socket
        if upstream in visited:
            return False
        visited.add(upstream)

        # Descend INTO a ShaderNodeGroup: find the matching Group Output
        # in the inner tree and walk that internal input.
        if upstream.bl_idname == "ShaderNodeGroup" and getattr(upstream, "node_tree", None):
            inner = upstream.node_tree
            output_node = None
            for n in inner.nodes:
                if n.bl_idname == "NodeGroupOutput" and getattr(n, "is_active_output", True):
                    output_node = n
                    break
            if output_node is None:
                for n in inner.nodes:
                    if n.bl_idname == "NodeGroupOutput":
                        output_node = n
                        break
            if output_node is None:
                return False
            inner_socket = None
            for inp in output_node.inputs:
                if inp.identifier == from_sock.identifier or inp.name == from_sock.name:
                    inner_socket = inp
                    break
            if inner_socket is None:
                return False
            return walk(inner_socket, group_stack + [upstream])

        # Ascend OUT of a ShaderNodeGroup via NodeGroupInput.
        if upstream.bl_idname == "NodeGroupInput":
            if not group_stack:
                return False
            outer_group = group_stack[-1]
            outer_socket = None
            for inp in outer_group.inputs:
                if inp.identifier == from_sock.identifier or inp.name == from_sock.name:
                    outer_socket = inp
                    break
            if outer_socket is None:
                return False
            return walk(outer_socket, group_stack[:-1])

        # Representable terminals halt cleanly (no bake required).
        if upstream.bl_idname in (
            "ShaderNodeTexImage",
            "ShaderNodeRGB",
            "ShaderNodeValue",
        ):
            return False

        # Descend through signal carriers via their named inputs only.
        carrier_pred = _SIGNAL_CARRIER_INPUTS.get(upstream.bl_idname)
        if carrier_pred is None:
            # Any other node type (HSV, Mix, RGBCurves, ColorRamp, ...) is
            # exactly the unrepresentable case we want to bake.
            return True
        for inp in upstream.inputs:
            if carrier_pred(inp.name):
                if walk(inp, group_stack):
                    return True
        # Carrier with no signal-carrier path through it -- still flat.
        return False

    return walk(base, [])


def find_unrepresentable_materials(scene: bpy.types.Scene) -> list[bpy.types.Material]:
    """Return all materials in the scene whose Base Color graph requires baking."""
    seen: set[str] = set()
    result: list[bpy.types.Material] = []
    for obj in scene.objects:
        if not getattr(obj, "material_slots", None):
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in seen:
                continue
            seen.add(mat.name)
            if _base_color_chain_is_unrepresentable(mat):
                result.append(mat)
    return result


def _find_render_object_for_material(
    scene: bpy.types.Scene, material: bpy.types.Material
) -> Optional[bpy.types.Object]:
    """Return a renderable mesh in `scene` whose UVs we can use as the bake
    domain for `material`. Prefers non-hidden, UV-mapped objects."""
    candidates: list[bpy.types.Object] = []
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        if not getattr(obj.data, "uv_layers", None) or len(obj.data.uv_layers) == 0:
            continue
        if not any(s.material == material for s in obj.material_slots):
            continue
        candidates.append(obj)
    if not candidates:
        return None
    # Prefer visible ones.
    visible = [o for o in candidates if not o.hide_render and not o.hide_get()]
    return (visible or candidates)[0]


def _ensure_bake_target_node(
    material: bpy.types.Material, image: bpy.types.Image
) -> bpy.types.Node:
    """Add a temporary Image Texture node bound to `image` and make it
    the active selected node so `bpy.ops.object.bake` writes into it.
    Returns the created node (caller must remove it)."""
    nt = material.node_tree
    img_node = nt.nodes.new("ShaderNodeTexImage")
    img_node.image = image
    img_node.label = "_MirisBakeTarget"
    img_node.name = "_MirisBakeTarget"
    # Mark as active selected so Cycles uses it as the bake target.
    for n in nt.nodes:
        n.select = False
    img_node.select = True
    nt.nodes.active = img_node
    return img_node


def bake_diffuse_color(
    obj: bpy.types.Object,
    material: bpy.types.Material,
    output_path: str,
    resolution: int = 1024,
) -> bool:
    """Bake `material`'s diffuse color (no lighting) into a PNG at
    `output_path`. Uses the supplied `obj` as the bake domain so its UVs
    define the texture's parameterisation. Returns True on success."""
    scene = bpy.context.scene
    prev_engine = scene.render.engine
    scene.render.engine = "CYCLES"
    scene.cycles.bake_type = "DIFFUSE"
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True
    scene.render.bake.margin = 4

    # Make the object active + selected; isolation guarantees we bake
    # the right material chain (Cycles uses the active material slot's
    # active Image Texture node as the bake target).
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Force the material to be the only one we bake into. We make the
    # bake-target image, then pick the slot index that holds this
    # material so the bake walks the right shader graph.
    image_name = f"_miris_bake_{material.name}"
    if image_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[image_name])
    image = bpy.data.images.new(
        name=image_name,
        width=resolution,
        height=resolution,
        alpha=False,
        float_buffer=False,
    )
    image.colorspace_settings.name = "sRGB"

    bake_node = _ensure_bake_target_node(material, image)
    try:
        # Activate the material slot containing `material` so the bake op
        # picks our just-added bake-target node.
        for idx, slot in enumerate(obj.material_slots):
            if slot.material == material:
                obj.active_material_index = idx
                break

        try:
            bpy.ops.object.bake(type="DIFFUSE")
        except RuntimeError as e:
            print(f"[bake-unrep-albedo] bake op failed for {material.name}: {e}")
            return False

        # Save the image to disk.
        image.filepath_raw = output_path
        image.file_format = "PNG"
        image.save()
        return os.path.isfile(output_path)
    finally:
        # Always clean up the temp node (USD already exported, no harm
        # to the source .blend either since we don't save it).
        try:
            material.node_tree.nodes.remove(bake_node)
        except Exception:
            pass


# --- USD-side rewiring ------------------------------------------------------
def _import_pxr():
    """Late-import so we don't pay the cost when nothing is to be baked."""
    from pxr import Usd, UsdGeom, UsdShade, Sdf  # type: ignore
    return Usd, UsdGeom, UsdShade, Sdf


def rewire_diffuse_color(
    stage_path: str,
    material_name: str,
    baked_image_relpath: str,
    uv_varname: str = "st",
) -> bool:
    """Open the USD at `stage_path`, find the material whose prim name
    matches `material_name`, and rewire its UsdPreviewSurface `diffuseColor`
    input to a fresh UsdUVTexture pointing at `baked_image_relpath`
    (resolved relative to the stage). Returns True if the rewire was
    applied and the stage saved."""
    Usd, UsdGeom, UsdShade, Sdf = _import_pxr()

    stage = Usd.Stage.Open(stage_path)
    if stage is None:
        return False

    # USD sanitizes prim names (hyphen -> underscore, etc.). Build a set of
    # plausible prim names from the Blender material name and accept a match
    # on any of them.
    candidates = {material_name}
    sanitized = material_name.replace("-", "_").replace(".", "_").replace(" ", "_")
    candidates.add(sanitized)

    target_mat = None
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material) and prim.GetName() in candidates:
            target_mat = UsdShade.Material(prim)
            break
    if target_mat is None:
        return False

    # Find the UsdPreviewSurface shader (info:id == "UsdPreviewSurface").
    preview = None
    for child in target_mat.GetPrim().GetChildren():
        if not child.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(child)
        id_attr = shader.GetIdAttr()
        if id_attr.Get() == "UsdPreviewSurface":
            preview = shader
            break
    if preview is None:
        return False

    diffuse_in = preview.GetInput("diffuseColor")
    if diffuse_in is None:
        diffuse_in = preview.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
    # Disconnect anything already there.
    diffuse_in.DisconnectSource()

    # Create / re-create a UsdUVTexture child for the baked file.
    mat_path = target_mat.GetPath()
    tex_name = "diffuseColor_baked_tex"
    pr_name = "diffuseColor_baked_st"
    tex_path = mat_path.AppendChild(tex_name)
    pr_path = mat_path.AppendChild(pr_name)
    # Remove any prior nodes from a previous bake.
    if stage.GetPrimAtPath(tex_path):
        stage.RemovePrim(tex_path)
    if stage.GetPrimAtPath(pr_path):
        stage.RemovePrim(pr_path)

    tex_shader = UsdShade.Shader.Define(stage, tex_path)
    tex_shader.CreateIdAttr("UsdUVTexture")
    tex_shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath(baked_image_relpath)
    )
    tex_shader.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
    tex_shader.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
    tex_shader.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
    rgb_out = tex_shader.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)

    pr_shader = UsdShade.Shader.Define(stage, pr_path)
    pr_shader.CreateIdAttr("UsdPrimvarReader_float2")
    pr_shader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(uv_varname)
    pr_out = pr_shader.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    tex_shader.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(pr_out)
    diffuse_in.ConnectToSource(rgb_out)

    stage.GetRootLayer().Save()
    return True


def rewire_materialx_base_color(
    stage_path: str,
    material_name: str,
    baked_image_relpath: str,
    uv_varname: str = "st",
) -> bool:
    """Make the MaterialX surface arc of `material_name` faithful to the
    baked diffuse texture. Critter's body_purple / body_mouth_bag emit
    `mtlx:surface ← node_137 (ND_surface)` with a complex `bsdf`/`edf`
    subgraph that Karma renders to either an under-saturated or broken
    color (the painterly chain has Light-Path / Mix Shader / vertex-color
    pieces that don't survive MaterialX inlining cleanly). Replace the
    `mtlx:surface` connection with a freshly minted
    ND_open_pbr_surface_surfaceshader whose `base_color` reads the baked
    PNG via UsdUVTexture → UsdPrimvarReader_float2, so MaterialX-consuming
    renderers (Karma, Storm with mtlx) see the same painterly diffuse
    that Cycles produced. If the existing mtlx surface is already
    ND_open_pbr_surface_surfaceshader (e.g. critter_tongue), just rewire
    its base_color input in place. Returns True if the stage was edited
    and saved."""
    Usd, UsdGeom, UsdShade, Sdf = _import_pxr()

    stage = Usd.Stage.Open(stage_path)
    if stage is None:
        return False

    candidates = {material_name}
    sanitized = material_name.replace("-", "_").replace(".", "_").replace(" ", "_")
    candidates.add(sanitized)

    target_mat = None
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material) and prim.GetName() in candidates:
            target_mat = UsdShade.Material(prim)
            break
    if target_mat is None:
        return False

    mtlx_token = "mtlx"
    mtlx_out = target_mat.GetSurfaceOutput(mtlx_token)
    if not mtlx_out:
        # No MaterialX surface arc on this material -- nothing to do.
        return False

    mat_path = target_mat.GetPath()
    tex_name = "baseColor_mtlx_baked_tex"
    pr_name = "baseColor_mtlx_baked_st"
    tex_path = mat_path.AppendChild(tex_name)
    pr_path = mat_path.AppendChild(pr_name)
    # Re-create from scratch in case a prior bake left stale prims.
    if stage.GetPrimAtPath(tex_path):
        stage.RemovePrim(tex_path)
    if stage.GetPrimAtPath(pr_path):
        stage.RemovePrim(pr_path)

    # MaterialX UsdMtlxRead consumers expect the texture stack to be
    # ND_image_color3 + ND_geompropvalue_vector2 to round-trip through
    # the same `mtlx` shader render context that drives `node_137`.
    pr_shader = UsdShade.Shader.Define(stage, pr_path)
    pr_shader.CreateIdAttr("ND_geompropvalue_vector2")
    pr_shader.CreateInput("geomprop", Sdf.ValueTypeNames.String).Set(uv_varname)
    pr_out = pr_shader.CreateOutput("out", Sdf.ValueTypeNames.Float2)

    tex_shader = UsdShade.Shader.Define(stage, tex_path)
    tex_shader.CreateIdAttr("ND_image_color3")
    tex_shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath(baked_image_relpath)
    )
    tex_shader.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(pr_out)
    tex_out = tex_shader.CreateOutput("out", Sdf.ValueTypeNames.Color3f)

    # Is the existing mtlx surface already ND_open_pbr_surface_surfaceshader?
    surf_shader = None
    if mtlx_out.HasConnectedSource():
        src = mtlx_out.GetConnectedSource()
        if src and src[0] is not None:
            cand = UsdShade.Shader(src[0].GetPrim())
            id_val = cand.GetIdAttr().Get()
            if id_val == "ND_open_pbr_surface_surfaceshader":
                surf_shader = cand

    if surf_shader is not None:
        # In-place rewire of base_color.
        bc_in = surf_shader.GetInput("base_color")
        if bc_in is None:
            bc_in = surf_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f)
        bc_in.DisconnectSource()
        bc_in.ConnectToSource(tex_out)
    else:
        # Replace the surface arc with a fresh open_pbr_surface that
        # carries our baked diffuse. The complex prior graph (ND_surface
        # with custom bsdf/edf wiring) renders to a non-faithful result
        # under MaterialX consumers; the bake captures Cycles' final
        # painterly diffuse, so a clean open_pbr_surface base_color is
        # the most faithful expression of artist intent.
        new_name = "baseColor_mtlx_baked_surface"
        new_path = mat_path.AppendChild(new_name)
        if stage.GetPrimAtPath(new_path):
            stage.RemovePrim(new_path)
        new_surf = UsdShade.Shader.Define(stage, new_path)
        new_surf.CreateIdAttr("ND_open_pbr_surface_surfaceshader")
        bc_in = new_surf.CreateInput("base_color", Sdf.ValueTypeNames.Color3f)
        bc_in.ConnectToSource(tex_out)
        new_out = new_surf.CreateOutput("out", Sdf.ValueTypeNames.Token)
        mtlx_out.DisconnectSource()
        mtlx_out.ConnectToSource(new_out)

    stage.GetRootLayer().Save()
    return True


# --- Public driver ----------------------------------------------------------
def bake_and_export(filepath: str, only_materials: Optional[Iterable[str]] = None,
                    bake_resolution: int = 1024, **wm_usd_export_kwargs) -> dict:
    """Top-level driver.

    1) For each material in the scene whose Base Color is unrepresentable
       (per the exporter allowlist), bake diffuse to a PNG named
       `<usd_basename>_bake_<material>.png` next to `filepath`.
    2) Run `bpy.ops.wm.usd_export(filepath=filepath, **wm_usd_export_kwargs)`.
    3) For each baked material, rewire the USD's UsdPreviewSurface
       diffuseColor to a UsdUVTexture pointing at the baked PNG.

    Returns: { material_name: baked_png_path, ... } for materials we
    actually baked + rewired. Materials we detected as unrepresentable
    but couldn't bake (no UV-mapped object, bake op failed) are not in
    the returned dict.
    """
    scene = bpy.context.scene
    out_dir = os.path.dirname(os.path.abspath(filepath))
    base = os.path.splitext(os.path.basename(filepath))[0]
    os.makedirs(out_dir, exist_ok=True)

    candidates = find_unrepresentable_materials(scene)
    if only_materials is not None:
        names = set(only_materials)
        candidates = [m for m in candidates if m.name in names]

    print(f"[bake-unrep-albedo] candidates: {[m.name for m in candidates]}")

    baked: dict[str, str] = {}
    for mat in candidates:
        obj = _find_render_object_for_material(scene, mat)
        if obj is None:
            print(f"[bake-unrep-albedo] no UV-mapped object found for {mat.name}; skipping")
            continue
        png = os.path.join(out_dir, f"{base}_bake_{mat.name}.png")
        ok = bake_diffuse_color(obj, mat, png, resolution=bake_resolution)
        if ok:
            baked[mat.name] = png
            print(f"[bake-unrep-albedo] baked {mat.name} -> {png}")
        else:
            print(f"[bake-unrep-albedo] bake failed for {mat.name}")

    # Run the USD export.
    bpy.ops.wm.usd_export(filepath=filepath, **wm_usd_export_kwargs)

    # Rewire the USD. Try both the UsdPreviewSurface (diffuseColor) and
    # the MaterialX surface (open_pbr_surface.base_color) — the former
    # may be absent under emit_preview_surface_alongside_materialx=False
    # (PR #35's default suppresses dual arcs when MaterialX is present);
    # the latter is what Karma reads when MaterialX is the active arc.
    for mat_name, png in list(baked.items()):
        relpath = os.path.relpath(png, out_dir)
        preview_ok = rewire_diffuse_color(filepath, mat_name, relpath)
        mtlx_ok = rewire_materialx_base_color(filepath, mat_name, relpath)
        if not preview_ok and not mtlx_ok:
            print(f"[bake-unrep-albedo] rewire failed for {mat_name} (neither preview nor mtlx); removing")
            baked.pop(mat_name, None)
        else:
            tag = []
            if preview_ok:
                tag.append("UsdPreviewSurface.diffuseColor")
            if mtlx_ok:
                tag.append("mtlx.open_pbr_surface.base_color")
            print(f"[bake-unrep-albedo] rewired {mat_name} -> {relpath} via {', '.join(tag)}")

    return baked
