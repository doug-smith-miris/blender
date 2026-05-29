"""Regression lock for `success:blender-fork-simple-pbr-mtlx-roundtrip`.

This is a *success sentinel* (a capstone regression lock, not a bug fix). It guards the
dual-output invariant that every prior Miris material fix (PRs #2-#20) implicitly relies
on: when a material is exported with BOTH preview-surface and MaterialX networks enabled,
the resulting USD `Material` prim must carry TWO surface output arcs that agree on the
core PBR parameters:

    outputs:surface      -> a UsdPreviewSurface shader      (id "UsdPreviewSurface")
    outputs:mtlx:surface -> a MaterialX OpenPBR shader       (id "ND_open_pbr_surface_*")

and those two shaders must round-trip the source Principled-BSDF's constant
Base Color / Metallic / Roughness / IOR, mapped onto each schema's native input names:

    Blender Principled    UsdPreviewSurface     MaterialX open_pbr_surface
    ------------------    -----------------     --------------------------
    Base Color            diffuseColor          base_color
    Metallic              metallic              base_metalness
    Roughness             roughness             specular_roughness
    IOR                   ior                   specular_ior

Per the hard rule this is driven by a REAL AYON hero (Project Gold `mikassa-v001.blend`),
isolated to a single constant-value Principled slot -- `mika-ornaments_metal`, a direct
Principled BSDF whose Base Color / Metallic / Roughness / IOR are all literal constants
(no texture wiring), bound to the six `GEO-mika-hair_ornament_*` meshes. Using a
constant-value slot makes the round-trip values unambiguous: any divergence between the
two arcs (or against the source) is a real regression, not a sampling artifact of a
painterly/texture network.

The Karma-compiles-clean half of the invariant (no `Error 1067: Reference to undefined
variable`) is asserted by the surrounding harness via `husk` on the exported MaterialX
USD -- it cannot be checked from inside Blender's `bpy`.

Run (MUST use the patched build):
  <patched>/Blender --background <mikassa-v001.blend> --python this_file.py
"""
import sys

import bpy

TARGET_MAT = "mika-ornaments_metal"
PREVIEW_OUT = "/tmp/test_simple_pbr_roundtrip_preview.usda"
MTLX_OUT = "/tmp/test_simple_pbr_roundtrip_mtlx.usda"

# Tolerances: color/scalar comparisons after the (linear) sRGB-agnostic export.
RTOL = 1e-3


def _principled_constants(mat):
    """Read the source Principled BSDF's constant Base Color / Metallic / Roughness / IOR."""
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    out = out or next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    assert out and out.inputs["Surface"].links, "%s has no active Material Output" % mat.name
    bsdf = out.inputs["Surface"].links[0].from_node
    assert bsdf.type == "BSDF_PRINCIPLED", "%s surface is not a direct Principled BSDF" % mat.name
    for chan in ("Base Color", "Metallic", "Roughness", "IOR"):
        assert not bsdf.inputs[chan].links, (
            "%s.%s is texture-driven; this sentinel requires a constant-value slot" % (mat.name, chan)
        )
    bc = bsdf.inputs["Base Color"].default_value
    return {
        "base_color": (bc[0], bc[1], bc[2]),
        "metallic": bsdf.inputs["Metallic"].default_value,
        "roughness": bsdf.inputs["Roughness"].default_value,
        "ior": bsdf.inputs["IOR"].default_value,
    }


def _export(filepath, materialx):
    bpy.ops.object.select_all(action="DESELECT")
    n = 0
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if any(s.material and s.material.name == TARGET_MAT for s in obj.material_slots):
            obj.hide_viewport = False
            obj.hide_render = False
            obj.select_set(True)
            n += 1
    assert n > 0, "no mesh carries material %r" % TARGET_MAT
    bpy.ops.wm.usd_export(
        filepath=filepath,
        check_existing=False,
        selected_objects_only=True,
        export_animation=False,
        export_materials=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        generate_materialx_network=materialx,
        # This success sentinel locks the *dual-output* invariant — explicitly opt back
        # into the legacy behavior so the UsdPreviewSurface arc is still authored even
        # when MaterialX has produced a usable `mtlx:surface`. See bite
        # BL-MAT-MATX-SHADOWS-PREVIEWSURFACE for the rationale behind the new default.
        emit_preview_surface_alongside_materialx=True,
        root_prim_path="/root",
    )


def _close(a, b):
    return abs(float(a) - float(b)) <= RTOL + RTOL * abs(float(b))


def _vec_close(a, b):
    return all(_close(x, y) for x, y in zip(a, b))


def main():
    src = bpy.data.materials.get(TARGET_MAT)
    assert src is not None, "target material %r not found in %s" % (TARGET_MAT, bpy.data.filepath)
    expected = _principled_constants(src)
    print("Source Principled constants:", expected)

    _export(PREVIEW_OUT, materialx=False)
    _export(MTLX_OUT, materialx=True)
    print("USD_EXPORT_PATH:", PREVIEW_OUT)
    print("USD_EXPORT_PATH_MTLX:", MTLX_OUT)

    from pxr import Usd, UsdShade

    preview = Usd.Stage.Open(PREVIEW_OUT)
    mtlx = Usd.Stage.Open(MTLX_OUT)

    def find_material(stage):
        hit = [p for p in stage.Traverse()
               if p.GetTypeName() == "Material" and "ornaments_metal" in p.GetName()]
        assert hit, "exported stage has no %r Material prim" % TARGET_MAT
        return UsdShade.Material(hit[0])

    pm = find_material(preview)
    mm = find_material(mtlx)

    # --- (1) outputs:surface -> UsdPreviewSurface arc exists --------------------
    psrc = pm.ComputeSurfaceSource()
    psh = psrc[0] if psrc else None
    assert psh, "%s: no outputs:surface (UsdPreviewSurface) source" % pm.GetPath()
    pid = str(psh.GetIdAttr().Get() or "")
    assert pid == "UsdPreviewSurface", "preview surface id is %r, expected UsdPreviewSurface" % pid
    print("UsdPreviewSurface arc:", psh.GetPath(), pid)

    # --- (2) outputs:mtlx:surface -> MaterialX OpenPBR arc exists ---------------
    msrc = mm.ComputeSurfaceSource("mtlx")
    msh = msrc[0] if msrc else None
    assert msh, "%s: no outputs:mtlx:surface (MaterialX) source" % mm.GetPath()
    mid = str(msh.GetIdAttr().Get() or "")
    assert mid.startswith("ND_open_pbr_surface"), (
        "mtlx surface id is %r, expected an OpenPBR open_pbr_surface node" % mid
    )
    print("MaterialX OpenPBR arc:", msh.GetPath(), mid)

    def pin(name):
        return psh.GetInput(name).Get()

    def min_(name):
        return msh.GetInput(name).Get()

    # --- (3) both arcs round-trip the source Base Color / Metallic / Roughness / IOR
    checks = [
        ("Base Color", expected["base_color"], pin("diffuseColor"), min_("base_color"), _vec_close),
        ("Metallic", expected["metallic"], pin("metallic"), min_("base_metalness"), _close),
        ("Roughness", expected["roughness"], pin("roughness"), min_("specular_roughness"), _close),
        ("IOR", expected["ior"], pin("ior"), min_("specular_ior"), _close),
    ]
    for label, exp, pval, mval, cmp in checks:
        print("  %-10s source=%s  preview=%s  mtlx=%s" % (label, exp, pval, mval))
        assert pval is not None, "UsdPreviewSurface missing %s" % label
        assert mval is not None, "MaterialX OpenPBR missing %s" % label
        assert cmp(pval, exp), "%s: UsdPreviewSurface %s != source %s" % (label, pval, exp)
        assert cmp(mval, exp), "%s: MaterialX %s != source %s" % (label, mval, exp)
        assert cmp(pval, mval), "%s: preview %s != mtlx %s (arcs disagree)" % (label, pval, mval)

    print("PASS: simple-pbr mtlx round-trip — both surface arcs agree with the source "
          "Principled BSDF on Base Color / Metallic / Roughness / IOR")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
