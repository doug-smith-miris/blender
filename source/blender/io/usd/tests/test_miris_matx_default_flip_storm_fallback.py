"""Regression sentinel for `matx-default-flip-storm-fallback`.

Direct follow-on to PR #35 (BL-MAT-MATX-SHADOWS-PREVIEWSURFACE): with the new
`emit_preview_surface_alongside_materialx=False` default, exports that author at
least one MaterialX-only surface arc must stamp a stage-level marker so downstream
USD consumers without MaterialX support (older Hydra Storm, certain Omniverse
builds) can detect and warn instead of silently losing all shading.

The marker is `customLayerData["miris:requiresMaterialX"] = True` on the root
layer (`SdfLayer.SetCustomLayerData` / `GetCustomLayerData`) — cheap to read,
no shader-graph traversal required by consumers.

Three invariants on REAL critter-v001.blend (Project Singularity):

  (A) Default export (mtlx-only by default per PR #35) → marker IS authored.
  (B) `generate_materialx_network=False` export (only UsdPreviewSurface arcs,
      no mtlx-only materials by construction)            → marker is NOT authored.
  (C) Opt-in dual-arc (emit_preview_surface_alongside_materialx=True) on a corpus
      where SOME materials still ship mtlx-only (critter's `inner_blue` family —
      PR #35 caveat) → marker IS still authored (it tracks the structural
      condition "the layer has at least one mtlx-only material", which is what
      downstream tools care about).

Run (MUST use the patched build):
  <patched>/Blender --background <critter-v001.blend> --python this_file.py
"""

import sys

import bpy

CRITTER_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/assets/char/critter/"
    "publish/critter-v001.blend"
)

OUT_DEFAULT = "/tmp/test_matx_default_flip_storm_fallback_default.usda"
OUT_NO_MTLX = "/tmp/test_matx_default_flip_storm_fallback_no_mtlx.usda"
OUT_OPTIN = "/tmp/test_matx_default_flip_storm_fallback_optin.usda"


def _export(filepath, *, generate_materialx_network, emit_preview_surface_alongside_materialx):
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
    bpy.ops.wm.usd_export(
        filepath=filepath,
        check_existing=False,
        selected_objects_only=False,
        export_animation=False,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        generate_materialx_network=generate_materialx_network,
        emit_preview_surface_alongside_materialx=emit_preview_surface_alongside_materialx,
        root_prim_path="/root",
    )


def _has_requires_materialx_marker(filepath):
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(filepath)
    assert layer is not None, "Failed to open exported layer: %s" % filepath
    data = layer.customLayerData
    return bool(data.get("miris:requiresMaterialX", False))


def _mtlx_only_material_count(filepath):
    """Count materials with a connected mtlx:surface but no connected universal surface."""
    from pxr import Usd, UsdShade

    stage = Usd.Stage.Open(filepath)
    assert stage is not None, "Failed to open exported stage: %s" % filepath

    count = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Material":
            continue
        mat = UsdShade.Material(prim)
        mtlx_src = mat.ComputeSurfaceSource("mtlx")
        if not (mtlx_src and mtlx_src[0]):
            continue
        univ_src = mat.ComputeSurfaceSource()
        if not (univ_src and univ_src[0]):
            count += 1
    return count


def main():
    if bpy.data.filepath == "":
        bpy.ops.wm.open_mainfile(filepath=CRITTER_BLEND)

    _export(OUT_DEFAULT, generate_materialx_network=True, emit_preview_surface_alongside_materialx=False)
    _export(OUT_NO_MTLX, generate_materialx_network=False, emit_preview_surface_alongside_materialx=False)
    _export(OUT_OPTIN, generate_materialx_network=True, emit_preview_surface_alongside_materialx=True)

    print("USD_EXPORT_PATH_DEFAULT:", OUT_DEFAULT)
    print("USD_EXPORT_PATH_NO_MTLX:", OUT_NO_MTLX)
    print("USD_EXPORT_PATH_OPTIN:", OUT_OPTIN)

    # --- (A) Default export: mtlx-only materials exist → marker IS authored.
    default_mtlx_only = _mtlx_only_material_count(OUT_DEFAULT)
    default_marker = _has_requires_materialx_marker(OUT_DEFAULT)
    print("  default: mtlx_only_materials=%d  marker=%s" % (default_mtlx_only, default_marker))
    assert default_mtlx_only > 0, (
        "Default export produced no mtlx-only materials at all on critter — the "
        "test would be a no-op. Did PR #35 regress?"
    )
    assert default_marker, (
        "Default export has %d mtlx-only materials but customLayerData "
        "'miris:requiresMaterialX' was NOT authored — downstream Storm / non-MTLX "
        "consumers cannot detect that they'd silently drop shading on those "
        "materials." % default_mtlx_only
    )

    # --- (B) MaterialX disabled: every material is a UsdPreviewSurface arc, no
    #          mtlx-only materials by construction → marker NOT authored.
    no_mtlx_count = _mtlx_only_material_count(OUT_NO_MTLX)
    no_mtlx_marker = _has_requires_materialx_marker(OUT_NO_MTLX)
    print("  no-mtlx: mtlx_only_materials=%d  marker=%s" % (no_mtlx_count, no_mtlx_marker))
    assert no_mtlx_count == 0, (
        "generate_materialx_network=False export produced mtlx-only materials "
        "(%d) — that should be impossible by construction." % no_mtlx_count
    )
    assert not no_mtlx_marker, (
        "generate_materialx_network=False export has no mtlx-only materials but "
        "authored the 'miris:requiresMaterialX' marker anyway — false positive "
        "that would scare downstream Storm consumers off a layer they CAN render."
    )

    # --- (C) Opt-in dual-arc: some critter materials (inner_blue family — see
    #          PR #35 caveat: find_bsdf_node can't reach a BSDF for these so
    #          they ship mtlx-only even with the opt-in flag) remain mtlx-only,
    #          so the marker is still authored. This is the load-bearing
    #          invariant for downstream tooling: marker presence is a function
    #          of the LAYER's structural state, not of the export flag.
    optin_mtlx_only = _mtlx_only_material_count(OUT_OPTIN)
    optin_marker = _has_requires_materialx_marker(OUT_OPTIN)
    print("  opt-in:  mtlx_only_materials=%d  marker=%s" % (optin_mtlx_only, optin_marker))
    assert optin_mtlx_only > 0, (
        "Opt-in dual-arc export on critter dropped to zero mtlx-only materials — "
        "expected inner_blue family to remain mtlx-only per PR #35 caveat. "
        "If this changes the load-bearing invariant still holds (no mtlx-only "
        "→ no marker), but the (C) case stops exercising the production path."
    )
    assert optin_marker, (
        "Opt-in dual-arc export still has %d mtlx-only materials but the marker "
        "was NOT authored — the (C) invariant is broken; downstream tooling on "
        "an opt-in export would miss the warning condition." % optin_mtlx_only
    )

    print()
    print("PASS: matx-default-flip-storm-fallback — root-layer customLayerData "
          "'miris:requiresMaterialX'=True is authored iff at least one mtlx-only "
          "material lands in the export.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
