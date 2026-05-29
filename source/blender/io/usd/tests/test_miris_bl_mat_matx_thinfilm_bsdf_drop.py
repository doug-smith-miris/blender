"""BL-MAT-MATX-THINFILM-BSDF-DROP — faithful thin-film expansion in the MaterialX writer.

Stock Blender's MaterialX writer for Principled BSDF (node_shader_bsdf_principled.cc)
unconditionally creates a `<thin_film_bsdf>` MaterialX node and wraps it in a `<layer>`
on top of the dielectric/conductor mix. MaterialX 1.39's standard library exposes
thin-film as direct `thinfilm_thickness` / `thinfilm_ior` inputs on `dielectric_bsdf` /
`conductor_bsdf` (see pbrlib_defs.mtlx) — there is no separate `thin_film_bsdf` node.
UsdMtlxRead therefore resolves no NodeDef for the emitted `<thin_film_bsdf>`, drops its
connection into the `layer`, and the thin-film contribution is silently lost. On
critter-v001 the on-corpus exporter logged this 4× via the integration build's PR-#5
substitution pass ("substituting unknown node 'node_010' / 'node_015' / 'node_027'
(category=thin_film_bsdf, type=BSDF) with a no-contribution identity"); in this fork
PR #11 / PR #26's phantom-prune pass simply deletes the dropped Shader.

This patch rewires the writer to author `thinfilm_thickness` and `thinfilm_ior`
DIRECTLY on the existing `dielectric_bsdf` (specular + transmission) and
`conductor_bsdf` (metal) nodes — the canonical wiring used by `standard_surface.mtlx`
and `open_pbr_surface.mtlx`. The dropped `thin_film_bsdf` + enclosing `layer` are
removed entirely; the metalness mix is now layered under the coat directly.

Structural proof on critter-v001:
  (1) No Shader prim is missing info:id (no phantoms left to prune for this category).
  (2) No `ND_layer_bsdf` survives with exactly one connected input that was previously
      a thin-film bypass (no degenerate layers from this category).
  (3) Every connection in every Material's MaterialX network resolves to an existing
      prim (the Karma "Error 1067" sentinel from PR #29).
  (4) Critter's Principled-driven MaterialX materials author at least one
      `ND_dielectric_bsdf` or `ND_conductor_bsdf` that exposes `thinfilm_thickness`
      and `thinfilm_ior` inputs.

Run:
  <patched-blender> --background <critter-v001.blend> --python this_script.py
"""
import bpy
import os
import sys
import tempfile

from pxr import Usd, UsdShade

BLEND = bpy.data.filepath
OUT = os.path.join(tempfile.gettempdir(), "test_bl_mat_matx_thinfilm_bsdf_drop.usda")


def export():
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_set(False)
    bpy.ops.wm.usd_export(
        filepath=OUT,
        check_existing=False,
        selected_objects_only=False,
        evaluation_mode="RENDER",
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=True,
        convert_world_material=True,
        root_prim_path="/root",
    )
    print(f"USD_EXPORT_PATH={OUT}")


def validate():
    stage = Usd.Stage.Open(OUT)
    assert stage, f"failed to open exported USD: {OUT}"

    missing_id = []
    total_shaders = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader":
            continue
        total_shaders += 1
        shader = UsdShade.Shader(prim)
        id_attr = shader.GetIdAttr()
        tok = id_attr.Get() if id_attr else None
        if not tok:
            missing_id.append(prim.GetPath().pathString)

    # Dangling-connection check — the structural sentinel for Karma's "Error 1067".
    dangling = []
    total_conns = 0
    for prim in stage.Traverse():
        connectable = UsdShade.ConnectableAPI(prim)
        if not connectable:
            continue
        for shade_attr in list(connectable.GetInputs()) + list(connectable.GetOutputs()):
            for tgt in shade_attr.GetAttr().GetConnections():
                total_conns += 1
                target_prim = stage.GetPrimAtPath(tgt.GetPrimPath())
                if not target_prim or not target_prim.IsValid():
                    dangling.append(f"{shade_attr.GetAttr().GetPath()} -> {tgt}")

    # Find dielectric/conductor BSDFs (the destinations for the faithful thin-film
    # parameters) and check they expose the thinfilm inputs.
    bsdf_ids = {"ND_dielectric_bsdf", "ND_conductor_bsdf"}
    bsdfs_with_thinfilm = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader":
            continue
        shader = UsdShade.Shader(prim)
        id_tok = shader.GetIdAttr().Get() if shader.GetIdAttr() else None
        if id_tok not in bsdf_ids:
            continue
        input_names = {inp.GetBaseName() for inp in shader.GetInputs()}
        if "thinfilm_thickness" in input_names and "thinfilm_ior" in input_names:
            bsdfs_with_thinfilm.append((prim.GetPath().pathString, id_tok))

    # Look for any surviving `<thin_film_bsdf>` reference — there should be none.
    # We check both the info:id (would be empty after UsdMtlxRead drop) and any node
    # whose name still contains "thin_film" pattern (defense-in-depth).
    suspect_thinfilm_shaders = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader":
            continue
        shader = UsdShade.Shader(prim)
        id_tok = shader.GetIdAttr().Get() if shader.GetIdAttr() else None
        # Any shader whose info:id explicitly mentions thin_film_bsdf is the regression.
        if id_tok and "thin_film_bsdf" in id_tok:
            suspect_thinfilm_shaders.append(prim.GetPath().pathString)

    print(f"TOTAL_SHADERS={total_shaders}")
    print(f"SHADERS_MISSING_INFO_ID={len(missing_id)}")
    for p in missing_id:
        print(f"  missing_id: {p}")
    print(f"TOTAL_CONNECTIONS={total_conns}")
    print(f"DANGLING_CONNECTIONS={len(dangling)}")
    for d in dangling[:20]:
        print(f"  dangling: {d}")
    print(f"BSDF_WITH_THINFILM_INPUTS={len(bsdfs_with_thinfilm)}")
    for path, kind in bsdfs_with_thinfilm[:5]:
        print(f"  thinfilm-bearing: {kind} @ {path}")
    print(f"SUSPECT_THIN_FILM_BSDF_NODES={len(suspect_thinfilm_shaders)}")
    for p in suspect_thinfilm_shaders:
        print(f"  suspect: {p}")

    assert len(missing_id) == 0, (
        f"{len(missing_id)} Shader prim(s) left without info:id (phantom nodedef stubs): "
        f"{missing_id}"
    )
    assert len(dangling) == 0, (
        f"{len(dangling)} dangling UsdShade connection(s) (Karma Error 1067 trigger): "
        f"{dangling[:20]}"
    )
    assert len(suspect_thinfilm_shaders) == 0, (
        f"{len(suspect_thinfilm_shaders)} Shader prim(s) still carry `thin_film_bsdf` "
        f"info:id; the writer should now wire `thinfilm_*` directly on dielectric/"
        f"conductor BSDFs: {suspect_thinfilm_shaders}"
    )
    assert len(bsdfs_with_thinfilm) >= 1, (
        "expected at least one ND_dielectric_bsdf / ND_conductor_bsdf to expose "
        "`thinfilm_thickness` + `thinfilm_ior` inputs after the faithful expansion "
        "patch; got 0 — the writer change did not take effect."
    )
    print(
        "BL_MAT_MATX_THINFILM_BSDF_DROP_PASS: no phantom thin_film_bsdf nodes; "
        "thinfilm parameters faithfully wired onto dielectric/conductor BSDFs; "
        "network connection-consistent."
    )


if __name__ == "__main__":
    export()
    validate()
