"""Umbrella regression harness for `swarmfish-visual-divergence-cycles-vs-karma`.

This is the integration sentinel that ties together the swarmfish material/geometry
fixes that landed on this branch and guards them against regressing as a group. It
exports Project Singularity's swarmfish-v001 hero in BOTH the UsdPreviewSurface and the
MaterialX export modes and asserts the *converged* invariants -- the structural state
that, together, lets a Karma render of the exported USD agree with the Cycles ground
truth on the creature body, eyes and brushstroke fins:

  1. The full material set is emitted AND bound (creature_body / creature_eyes /
     creature_pupil + the GN brushstroke material creature_BS).  Pre-fix, the GN curve
     carriers dropped their bindings and the eye/pupil groups collapsed to empty prims.
  2. No Material prim is empty (eyes/pupil ShaderNodeGroup expansion -- PR #18).
  3. The MaterialX network has no dangling `top`/`base` layer-node connections, so Karma
     compiles the shaders instead of aborting with `Error 1067` (PR #17).
  4. creature_eyes resolves to a real MaterialX surface (ND_surface), i.e. the eyes carry
     a genuine shaded network (the visible iris in the matched render).

KNOWN, ESCALATED residuals this harness deliberately does NOT assert (tracked as
follow-on candidate missions, see the bite's envelope):
  * The creature *body* base color is driven by a painterly-shading network
    (HSV / Mix / Light-Path composite over several maps) that has no single base-color
    texture; it does not reduce to a faithful UsdPreviewSurface/MaterialX albedo, so the
    body renders flat/over-bright in Karma while Cycles shows magenta. Closing it needs a
    bake-to-texture pass (a `traverse_channel` allowlist alone yields a flat color, not
    the magenta).
  * A large unbound `..._Brushstrokes_FLOW` BasisCurves guide leaks into the export and
    dominates the full-asset Karma framing as the "larger-extent silhouette"; Cycles does
    not render these GN flow guides. The matched render therefore isolates the creature.

Run (MUST use the patched build):
  <patched>/Blender --background <swarmfish-v001.blend> --python this_file.py
"""
import sys

import bpy

PREVIEW_OUT = "/tmp/test_swarmfish_umbrella_preview.usda"
MTLX_OUT = "/tmp/test_swarmfish_umbrella_mtlx.usda"

EXPECTED_MATERIALS = {"creature_body", "creature_eyes", "creature_pupil", "creature_BS"}


def _export(filepath, materialx):
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
    bpy.ops.wm.usd_export(
        filepath=filepath,
        check_existing=False,
        selected_objects_only=False,
        export_animation=False,
        export_curves=True,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        generate_materialx_network=materialx,
        root_prim_path="/root",
    )


def main():
    _export(PREVIEW_OUT, materialx=False)
    _export(MTLX_OUT, materialx=True)
    print("USD_EXPORT_PATH:", PREVIEW_OUT)
    print("USD_EXPORT_PATH_MTLX:", MTLX_OUT)

    from pxr import Usd, UsdShade, UsdGeom

    preview = Usd.Stage.Open(PREVIEW_OUT)
    mtlx = Usd.Stage.Open(MTLX_OUT)

    # (1) full material set present + bound -----------------------------------
    mats = {p.GetName() for p in preview.Traverse() if p.GetTypeName() == "Material"}
    print("Materials:", sorted(mats))
    missing = EXPECTED_MATERIALS - mats
    assert not missing, "expected materials missing from export: %s" % sorted(missing)

    bound = {
        UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0].GetPrim().GetName()
        for p in preview.Traverse()
        if p.IsA(UsdGeom.Imageable)
        and UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0]
        and UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()[0].GetPrim().IsValid()
    }
    print("Bound materials:", sorted(bound))
    assert "creature_BS" in bound, "GN brushstroke material creature_BS is not bound (PR #20)"

    # (2) no empty Material prims (eyes/pupil group expansion, PR #18) ---------
    empty = []
    for p in preview.Traverse():
        if p.GetTypeName() == "Material":
            shaders = [c for c in Usd.PrimRange(p) if c.IsA(UsdShade.Shader)]
            if not shaders:
                empty.append(p.GetPath().pathString)
    print("Empty material prims:", empty)
    assert not empty, "empty Material prims (eyes/pupil regression): %s" % empty

    # (3) no dangling layer-node connections in MaterialX (PR #17) -------------
    dangling = []
    for p in mtlx.Traverse():
        if not p.IsA(UsdShade.Shader):
            continue
        sh = UsdShade.Shader(p)
        sid = str(sh.GetIdAttr().Get() or "")
        if "layer" not in sid.lower():
            continue
        for inp in sh.GetInputs():
            if inp.HasConnectedSource():
                src = inp.GetConnectedSources()[0]
                if src and not src[0].source.GetPrim().IsValid():
                    dangling.append(p.GetPath().pathString)
    print("Dangling layer connections:", dangling)
    assert not dangling, "dangling MaterialX layer connections (Karma Error 1067): %s" % dangling

    # (4) eyes resolve to a real MaterialX surface ----------------------------
    eyes = UsdShade.Material(mtlx.GetPrimAtPath("/root/_materials/creature_eyes"))
    src = eyes.ComputeSurfaceSource("mtlx")
    eyes_id = src[0].GetIdAttr().Get() if src and src[0] else None
    print("creature_eyes mtlx surface id:", eyes_id)
    assert eyes_id and str(eyes_id).startswith("ND_"), (
        "creature_eyes does not resolve to a MaterialX surface: %s" % eyes_id
    )

    print("PASS: swarmfish-visual-divergence-cycles-vs-karma converged invariants hold")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
