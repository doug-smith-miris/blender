"""Regression sentinel for `BL-MAT-MATX-SHADOWS-PREVIEWSURFACE`.

Karma 21.0.700 ALWAYS prefers MaterialX over UsdPreviewSurface when both surface arcs are
authored on the same Material prim (confirmed by the prior diagnostic run: 12 of 17 critter
materials carry both, all render through the `mtlx:surface` arc; the `outputs:surface` arc
is dead weight). This bite changes the default behavior: when `generate_materialx_network=True`
AND MaterialX has produced a valid `outputs:mtlx:surface`, the exporter SKIPS authoring the
UsdPreviewSurface fallback.

The legacy dual-output behavior is preserved behind an opt-in flag
`emit_preview_surface_alongside_materialx`; PR #23's simple-PBR roundtrip sentinel uses it
to keep locking the dual-output invariant for callers who explicitly request both.

Verified on REAL critter-v001.blend (Project Singularity).

Run (MUST use the patched build):
  <patched>/Blender --background <critter-v001.blend> --python this_file.py
"""

import sys

import bpy

CRITTER_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/assets/char/critter/"
    "publish/critter-v001.blend"
)

DEFAULT_OUT = "/tmp/test_matx_shadows_previewsurface_default.usda"
OPTIN_OUT = "/tmp/test_matx_shadows_previewsurface_optin.usda"


def _export(filepath, emit_preview_surface_alongside_materialx):
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
        generate_materialx_network=True,
        emit_preview_surface_alongside_materialx=emit_preview_surface_alongside_materialx,
        root_prim_path="/root",
    )


def main():
    if bpy.data.filepath == "":
        bpy.ops.wm.open_mainfile(filepath=CRITTER_BLEND)

    _export(DEFAULT_OUT, emit_preview_surface_alongside_materialx=False)
    _export(OPTIN_OUT, emit_preview_surface_alongside_materialx=True)
    print("USD_EXPORT_PATH:", DEFAULT_OUT)
    print("USD_EXPORT_PATH_OPTIN:", OPTIN_OUT)

    from pxr import Usd, UsdShade

    def _surface_arcs(stage):
        """Return list of (material_name, has_universal_surface, has_mtlx_surface)."""
        rows = []
        for prim in stage.Traverse():
            if prim.GetTypeName() != "Material":
                continue
            mat = UsdShade.Material(prim)
            universal_src = mat.ComputeSurfaceSource()
            mtlx_src = mat.ComputeSurfaceSource("mtlx")
            rows.append((
                prim.GetName(),
                bool(universal_src and universal_src[0]),
                bool(mtlx_src and mtlx_src[0]),
            ))
        return rows

    default_stage = Usd.Stage.Open(DEFAULT_OUT)
    optin_stage = Usd.Stage.Open(OPTIN_OUT)
    assert default_stage and optin_stage, "Failed to open exported stages"

    default_rows = _surface_arcs(default_stage)
    optin_rows = _surface_arcs(optin_stage)

    print()
    print("Default-mode (no opt-in) arcs:")
    for name, has_uni, has_mtlx in default_rows:
        print("  %-32s universal=%s mtlx=%s" % (name, has_uni, has_mtlx))

    # --- (1) Default mode: every material with a MaterialX surface must NOT also
    #         author a universal (`outputs:surface`) arc — that's the dead-weight
    #         this bite removes.
    dual_arcs = [name for name, has_uni, has_mtlx in default_rows if has_uni and has_mtlx]
    assert not dual_arcs, (
        "Default mode authored a UsdPreviewSurface arc alongside `mtlx:surface` for: %s — "
        "Karma ignores it (always picks mtlx), so it is dead weight. The new default should "
        "skip it." % dual_arcs
    )

    # --- (2) Default mode: at least one material reaches the MaterialX path (proves
    #         we're exercising the new behavior, not silently no-op'ing).
    mtlx_carriers = [name for name, has_uni, has_mtlx in default_rows if has_mtlx]
    assert mtlx_carriers, (
        "Default-mode export produced no `mtlx:surface` arcs at all on critter — the test "
        "would be a no-op."
    )
    print("  %d material(s) carry mtlx:surface; 0 also carry the universal arc (PASS)"
          % len(mtlx_carriers))

    # --- (3) Opt-in mode: legacy dual-output authoring still works on demand —
    #         materials whose preview-surface writer can find a BSDF should ALSO
    #         carry the universal surface arc alongside the mtlx one.
    #         (Materials whose top-level network has no Principled BSDF reachable from
    #         the Material Output via the find_bsdf_node walk — e.g. critter's
    #         `inner_blue` family which the preview-surface writer can't author —
    #         remain mtlx-only even with the opt-in flag; that's pre-existing behavior
    #         unrelated to this bite.)
    optin_dual = [name for name, has_uni, has_mtlx in optin_rows if has_uni and has_mtlx]
    optin_mtlx = [name for name, has_uni, has_mtlx in optin_rows if has_mtlx]
    assert optin_dual, (
        "Opt-in mode failed to author ANY dual-output materials — "
        "mtlx-carriers=%s dual-carriers=%s" % (optin_mtlx, optin_dual)
    )
    print("  opt-in mode: %d/%d mtlx-carrying materials also author universal arc"
          % (len(optin_dual), len(optin_mtlx)))

    # --- (3b) The opt-in flag is what flips the universal-arc count from 0 (default)
    #         to >0 — proving the flag actually re-enables the legacy behavior.
    default_dual = [name for name, has_uni, has_mtlx in default_rows if has_uni and has_mtlx]
    assert len(optin_dual) > len(default_dual), (
        "Opt-in flag did not increase dual-output count above the default: "
        "default=%d, opt-in=%d" % (len(default_dual), len(optin_dual))
    )

    # --- (4) Materials whose MaterialX network is degenerate / empty must still
    #         fall back to authoring a UsdPreviewSurface arc — the new default
    #         only suppresses the arc when MaterialX has produced a real surface.
    fallback_uni_only = [name for name, has_uni, has_mtlx in default_rows
                         if has_uni and not has_mtlx]
    print("  %d material(s) fall back to UsdPreviewSurface (no mtlx surface available)"
          % len(fallback_uni_only))
    # Don't assert — critter may have zero such materials; the assertion that matters
    # is (1) — dead-weight pairs must be zero.

    print()
    print("PASS: BL-MAT-MATX-SHADOWS-PREVIEWSURFACE — when MaterialX surface is authored, "
          "the UsdPreviewSurface arc is suppressed by default; opt-in flag re-enables the "
          "legacy dual-output contract.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
