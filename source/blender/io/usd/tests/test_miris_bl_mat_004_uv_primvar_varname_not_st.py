"""Regression test for BL-MAT-004 (UsdPrimvarReader_float2.varname not 'st').

USD's MaterialX / UsdPreviewSurface convention is that the default texture-coordinate
primvar is named `st`. Blender's mesh writer honours this: with `rename_uvmaps`
enabled it renames a mesh's *render* (default) UV map -- `Mesh::default_uv_map_name()`
-- to `st` when authoring the geometry primvar.

The material writer, however, fed `Mesh::active_uv_map_name()` (the editor-*selected*
UV map) as the "active uv map name" used to decide whether a `UsdPrimvarReader_float2`'s
`varname` should be rewritten to `st`. On swarmfish's `creature_body`, the
Geometry-Nodes-evaluated mesh's render UV map is named "Alignment" while the selected
(active) map differs, so:

  * the geometry primvar was renamed to `primvars:st`, but
  * the UV reader shader kept `string inputs:varname = "Alignment"`,

leaving a dangling UV reference -- the reader resolves nothing in renderers that require
the standard `st` name.

The fix in usd_writer_abstract.cc switches the material writer to
`Mesh::default_uv_map_name()` so it keys off the *same* UV map the mesh writer renames.

This test exports the real swarmfish asset and asserts that no `UsdPrimvarReader_float2`
in the stage references a primvar (`varname`) that is absent from the geometry it feeds.
In particular `creature_body` must no longer emit `varname = "Alignment"`.

Run headless with the PATCHED build:
  build_darwin/.../Blender --background <swarmfish.blend> \
      --python test_miris_bl_mat_004_uv_primvar_varname_not_st.py
"""

import os
import sys
import tempfile

import bpy
from pxr import Usd, UsdGeom, UsdShade

SWARMFISH_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/swarmfish/publish/swarmfish-v001.blend"
)

PRIMVAR_READER_2D = "UsdPrimvarReader_float2"


def export_swarmfish_preview():
    out_dir = tempfile.mkdtemp(prefix="miris_bl_mat_004_")
    usd_path = os.path.join(out_dir, "swarmfish-preview.usda")

    # Unhide everything so geometry-nodes driven meshes (and their materials) survive.
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)

    bpy.ops.wm.usd_export(
        filepath=usd_path,
        check_existing=False,
        selected_objects_only=False,
        export_materials=True,
        export_uvmaps=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        generate_materialx_network=False,
        rename_uvmaps=True,
        root_prim_path="/root",
    )
    return usd_path


def collect_geometry_uv_primvars(stage):
    """Set of every texCoord2f/float2 primvar name authored on any Mesh in the stage."""
    names = set()
    for prim in stage.Traverse():
        gp = UsdGeom.PrimvarsAPI(prim)
        for pv in gp.GetPrimvars():
            tn = pv.GetTypeName()
            if tn in (
                "texCoord2f[]",
                "float2[]",
                "texCoord2f",
                "float2",
            ):
                names.add(pv.GetPrimvarName())
    return names


def collect_uv_reader_varnames(stage):
    """Map of {shader prim path: varname value} for every UsdPrimvarReader_float2."""
    out = {}
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        if not shader:
            continue
        if shader.GetIdAttr().Get() != PRIMVAR_READER_2D:
            continue
        vn_input = shader.GetInput("varname")
        vn = vn_input.Get() if vn_input else None
        out[prim.GetPath().pathString] = str(vn) if vn is not None else None
    return out


def main():
    usd_path = export_swarmfish_preview()
    print("MIRIS_EXPORTED_USD:", usd_path)

    stage = Usd.Stage.Open(usd_path)
    assert stage, "Failed to open exported USD stage"

    geom_uvs = collect_geometry_uv_primvars(stage)
    readers = collect_uv_reader_varnames(stage)
    print("MIRIS_GEOM_UV_PRIMVARS:", sorted(geom_uvs))
    print("MIRIS_UV_READER_VARNAMES:", readers)

    assert readers, "No UsdPrimvarReader_float2 shaders found -- export looks broken"

    # Regression sentinel: stock Blender emits varname = "Alignment" on creature_body.
    alignment_refs = [p for p, vn in readers.items() if vn == "Alignment"]
    assert not alignment_refs, (
        "BL-MAT-004 regression: UsdPrimvarReader_float2 still references the raw "
        f"Blender UV name 'Alignment' (geometry primvar was renamed to 'st'): {alignment_refs}"
    )

    # Stronger invariant: every UV reader must point at a primvar that actually exists
    # on the exported geometry (the post-pass retargets dangling refs -- including the
    # sanitized empty "_" placeholders -- to the default "st" set).
    dangling = []
    for path, vn in readers.items():
        if not vn:
            continue
        if vn not in geom_uvs:
            dangling.append((path, vn))
    assert not dangling, (
        "UV readers reference primvars absent from geometry "
        f"(present on geom: {sorted(geom_uvs)}): {dangling}"
    )

    print(
        "MIRIS_TEST_PASS: every UsdPrimvarReader_float2.varname resolves to an existing "
        "geometry primvar; creature_body no longer dangles on 'Alignment'"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
