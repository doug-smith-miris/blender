"""Real-asset regression test for `propagate-karma-rendervisibility-to-bound-geom`.

Follow-on to PR #34 (BL-MAT-OPACITY-LIGHTPATH-DROP). That PR authored

    primvars:karma:object:rendervisibility = "camera,reflect,refract,diffuse,glossy,volume"

on the *Material* prim of any material whose Principled BSDF Alpha / Transmission
Weight is driven by a Light-Path / Transparent-BSDF visibility cutout. Karma,
however, reads this primvar from the GEOMETRY prim (UsdGeomMesh /
UsdGeomBasisCurves), not from the bound Material -- so the artist's "no-shadow"
intent stays invisible to the renderer until it reaches the geom. This bite
closes the loop in the mesh and curves writers: after `UsdShadeMaterialBindingAPI::Bind`,
the new `propagate_karma_object_rendervisibility` helper copies the primvar from
the bound Material onto the bound geom prim.

Regression sentinel (critter-v001.blend, hero asset from Project Singularity):
  - STOCK   /Applications/Blender.app : primvar is NOT on the geom prims that
                                        bind body_purple / body_mouth_bag, so
                                        Karma keeps casting solid shadows --
                                        test FAILS.
  - PATCHED build_darwin              : primvar present on the bound mesh prims,
                                        "shadow" excluded -- Karma drops those
                                        geoms from shadow rays as the artist
                                        intended -- test PASSES.

Adjacent invariants:
  - The PR #34 Material-level primvar is still authored (this bite is additive,
    not a replacement).
  - The PR #27 invariant -- `opacity = 1.0` on critter_tongue / body_purple /
    body_mouth_bag -- still holds (we touch material-binding only).
  - Meshes whose bound material is NOT a Light-Path cutout (every other slot on
    critter) do NOT get the primvar (no false positives).
"""

import os
import sys
import tempfile

import bpy
from pxr import Usd, UsdGeom, UsdShade

CRITTER_BLEND = (
    "/Users/d.smith/MirisProjects/AYON/Singularity Files/040_0030/"
    "assets/char/critter/publish/critter-v001.blend"
)

KARMA_VIS_ATTR = "primvars:karma:object:rendervisibility"
CUTOUT_MATERIALS = ("body_purple", "body_mouth_bag")
NON_CUTOUT_INVARIANT = "critter_tongue"


def export_critter_preview():
    out_dir = tempfile.mkdtemp(prefix="miris_propagate_karma_vis_")
    usd_path = os.path.join(out_dir, "critter-preview.usda")

    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)

    bpy.ops.wm.usd_export(
        filepath=usd_path,
        check_existing=False,
        selected_objects_only=False,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        generate_materialx_network=False,
        root_prim_path="/root",
    )
    return usd_path


def material_path_of(prim):
    """Return the path string of the directly-bound Material prim for `prim`,
    or None if not bound."""
    binding_api = UsdShade.MaterialBindingAPI(prim)
    rel = binding_api.GetDirectBindingRel()
    if not rel:
        return None
    targets = rel.GetTargets()
    if not targets:
        return None
    return str(targets[0])


def collect_carrier_meshes_for_materials(stage, material_names):
    """For each Material name in `material_names`, return the set of Mesh /
    BasisCurves geom prims that bind it -- either directly OR via a UsdGeomSubset
    descendant. Karma reads object-visibility primvars from the geometry prim,
    so the carrier-mesh level is the one we must verify.
    """
    result = {name: [] for name in material_names}
    for prim in stage.Traverse():
        is_geom = prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.BasisCurves)
        if not is_geom:
            continue
        bound_names = set()
        direct = material_path_of(prim)
        if direct is not None:
            bound_names.add(direct.rsplit("/", 1)[-1])
        # Scan immediate Subset children (UsdGeomSubsets live as children of the
        # carrying Mesh/Curves prim).
        for child in prim.GetChildren():
            if not child.IsA(UsdGeom.Subset):
                continue
            sub_mat = material_path_of(child)
            if sub_mat is not None:
                bound_names.add(sub_mat.rsplit("/", 1)[-1])
        for name in bound_names & set(material_names):
            result[name].append(prim)
    return result


def has_karma_vis_excluding_shadow(prim):
    """Return (authored: bool, value: Optional[str], excludes_shadow: bool).

    Karma's `primvars:karma:object:rendervisibility` syntax (see
    `BRAY_HdKarma_Geometry.ds`) uses pipe-separated ray flags with `-` prefix
    for exclusion. Tokens: `*`, `primary`, `shadow`, `reflect`, `refract`,
    `diffuse`, `glossy`, `volume`. `-shadow` (or `*&-shadow`, or `primary`)
    encodes "invisible to shadow rays"; a bare `*` does NOT exclude shadow.
    A comma-delimited token list with the `camera` alias parses as nothing
    recognised, dropping the object from primary rays as well.
    """
    attr = prim.GetAttribute(KARMA_VIS_ATTR)
    if not attr or not attr.IsAuthored():
        return (False, None, False)
    val = attr.Get()
    if val is None:
        return (True, None, False)
    val_str = str(val).strip()
    if val_str == "*":
        return (True, val_str, False)
    excludes_shadow = (
        val_str == "-shadow"
        or val_str == "primary"
        or "-shadow" in val_str
    )
    karma_syntax = (
        "-" in val_str
        or val_str.startswith("*")
        or "|" in val_str
        or val_str == "primary"
    )
    return (True, val_str, excludes_shadow and karma_syntax)


def main():
    usd_path = export_critter_preview()
    print("MIRIS_EXPORTED_USD:", usd_path)

    stage = Usd.Stage.Open(usd_path)
    assert stage, "Failed to open exported USD stage"

    # Map: material name -> material prim path.
    material_by_name = {}
    for prim in stage.Traverse():
        if UsdShade.Material(prim):
            material_by_name[prim.GetName()] = str(prim.GetPath())

    print(
        "MIRIS_CRITTER_MATERIALS:",
        sorted(material_by_name.keys()),
    )

    # PR #34 invariant: the cutout Materials still author the primvar on the Material
    # prim (this bite is additive).
    failures = []
    for mat_name in CUTOUT_MATERIALS:
        mat_path = material_by_name.get(mat_name)
        if mat_path is None:
            failures.append(f"Material `{mat_name}` not found in exported stage")
            continue
        mat_prim = stage.GetPrimAtPath(mat_path)
        authored, val, excludes = has_karma_vis_excluding_shadow(mat_prim)
        if not authored:
            failures.append(
                f"PR #34 regression: Material `{mat_name}` no longer authors "
                f"{KARMA_VIS_ATTR}"
            )
        elif not excludes:
            failures.append(
                f"PR #34 regression: Material `{mat_name}` {KARMA_VIS_ATTR}={val!r} "
                "no longer excludes shadow"
            )

    # New bite invariant: every Mesh / BasisCurves prim CARRYING a cutout material
    # (either directly bound, or via a UsdGeomSubset descendant) authors the primvar.
    # Karma reads object-visibility primvars from the geom level, not the subset.
    carriers_by_mat = collect_carrier_meshes_for_materials(
        stage, list(CUTOUT_MATERIALS) + [NON_CUTOUT_INVARIANT]
    )

    for mat_name in CUTOUT_MATERIALS:
        carriers = carriers_by_mat.get(mat_name, [])
        if not carriers:
            failures.append(
                f"No mesh/curves geometry carries `{mat_name}` (direct or via subset) "
                "-- cannot validate the propagation. Real critter export should bind "
                "these cutout materials to at least one geom prim."
            )
            continue
        for geom in carriers:
            authored, val, excludes = has_karma_vis_excluding_shadow(geom)
            print(
                f"MIRIS_CARRIER={geom.GetPath()} MAT={mat_name} authored={authored} "
                f"value={val!r} excludes_shadow={excludes}"
            )
            if not authored:
                failures.append(
                    f"Geom {geom.GetPath()} (carries cutout `{mat_name}`) is missing "
                    f"{KARMA_VIS_ATTR} -- Karma will still cast a shadow that "
                    "Cycles' Light-Path cutout suppresses"
                )
            elif not excludes:
                failures.append(
                    f"Geom {geom.GetPath()} (carries cutout `{mat_name}`) "
                    f"{KARMA_VIS_ATTR}={val!r} -- shadow not excluded"
                )

    # No-false-positive invariant: a non-cutout material (critter_tongue) and any
    # geom directly bound to it (with no cutout sibling) must NOT carry the primvar.
    tongue_path = material_by_name.get(NON_CUTOUT_INVARIANT)
    if tongue_path is not None:
        tongue_mat = stage.GetPrimAtPath(tongue_path)
        authored, val, _ = has_karma_vis_excluding_shadow(tongue_mat)
        if authored:
            failures.append(
                f"Adjacent: `{NON_CUTOUT_INVARIANT}` should not author "
                f"{KARMA_VIS_ATTR} (opacity is an unlinked constant; no cutout). "
                f"Got: {val!r}"
            )
        for geom in carriers_by_mat.get(NON_CUTOUT_INVARIANT, []):
            # Skip if the carrier also binds a cutout material (then primvar IS expected
            # because the artist authored the no-shadow intent on the carrying mesh).
            other_cutout = False
            for other in CUTOUT_MATERIALS:
                if geom in carriers_by_mat.get(other, []):
                    other_cutout = True
                    break
            if other_cutout:
                continue
            authored, val, _ = has_karma_vis_excluding_shadow(geom)
            if authored:
                failures.append(
                    f"Adjacent: geom {geom.GetPath()} (carries only non-cutout "
                    f"`{NON_CUTOUT_INVARIANT}`) should not carry {KARMA_VIS_ATTR}. "
                    f"Got: {val!r}"
                )

    # PR #27 invariant: opacity = 1.0 on the cutout materials (no regression here).
    for mat_name in CUTOUT_MATERIALS + (NON_CUTOUT_INVARIANT,):
        mat_path = material_by_name.get(mat_name)
        if mat_path is None:
            continue
        mat_prim = stage.GetPrimAtPath(mat_path)
        surface = None
        for child in mat_prim.GetChildren():
            shader = UsdShade.Shader(child)
            if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface":
                surface = shader
                break
        if surface is None:
            continue
        opacity_input = surface.GetInput("opacity")
        if opacity_input is None:
            continue
        val = opacity_input.Get()
        if val is None or abs(float(val) - 1.0) > 1e-6:
            failures.append(
                f"PR #27 regression: `{mat_name}` opacity = {val!r}, expected 1.0"
            )

    assert not failures, (
        "propagate-karma-rendervisibility-to-bound-geom NOT fixed:\n  "
        + "\n  ".join(failures)
    )

    print(
        "MIRIS_TEST_PASS: every geom bound to body_purple / body_mouth_bag carries "
        f"{KARMA_VIS_ATTR} with shadow excluded; critter_tongue's geom does not; "
        "PR #34 Material-level authoring intact; PR #27 opacity=1.0 invariant intact."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
