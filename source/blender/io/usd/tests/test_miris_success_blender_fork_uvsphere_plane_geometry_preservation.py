"""Regression lock for `success:blender-fork-uvsphere-plane-geometry-preservation`.

This is a *success sentinel* (a capstone regression lock, not a bug fix). It guards the
single-slot, simple-mesh-class geometry-preservation invariant in
`source/blender/io/usd/intern/usd_writer_mesh.cc`: a static polygon mesh exported through
`bpy.ops.wm.usd_export` must round-trip its authored topology and shape exactly --

    * points            == the evaluated mesh's vertex positions   (count + value, float eps)
    * faceVertexCounts  == the evaluated mesh's per-face loop totals (authored topology)
    * faceVertexIndices == the evaluated mesh's corner vertex indices
    * subdivisionScheme == "none"  (no spurious Catmull-Clark / no subdiv injected)
    * extent            == the local-space bbox of the points       (float eps)
    * normals           == the evaluated mesh's per-corner normals, authored faceVarying,
                           and SMOOTH shading is preserved (not flattened to face normals)

The original finding validated this on a synthetic UV-sphere + plane. Per the mission's
HARD RULE that synthetic fixtures be re-anchored onto real corpus content, this drives a
real Project Gold set asset: `rocks_layer_1.blend`, whose `GEO-rock_layer_1_*` rock meshes
are clean static polygon meshes -- NO modifiers, smooth-shaded, no subsurf -- i.e. exactly
the "simple mesh class" the sentinel guards. The target object carries one material slot,
so this also acts as a guard that the multi-material / GeomSubset work (PR #7), which lives
in the adjacent code, did not perturb single-slot topology output.

The evaluated mesh (RENDER depsgraph) is the ground truth: because the target has no
modifiers, evaluated topology == authored topology, so "matches the evaluated mesh" is the
same as "matches the authored topology" -- with no ambiguity from procedural realization.

Run (MUST use the patched build):
  <patched>/Blender --background <rocks_layer_1.blend> --python this_file.py
"""
import sys

import bpy

# A clean static rock mesh: no modifiers, smooth-shaded, no subsurf, single slot.
TARGET_OBJ = "GEO-rock_layer_1_medium_005"
OUT = "/tmp/test_geometry_preservation.usda"

POS_EPS = 1e-5   # absolute eps for local-space vertex positions / extent
NRM_EPS = 1e-4   # absolute eps for unit-length normals


def _pick_target():
    """Return the named static mesh, or fall back to the largest no-modifier smooth mesh."""
    obj = bpy.data.objects.get(TARGET_OBJ)
    if obj is not None and obj.type == "MESH":
        return obj
    cands = []
    for o in bpy.data.objects:
        if o.type != "MESH" or o.modifiers:
            continue
        me = o.data
        if len(me.polygons) > 0 and any(p.use_smooth for p in me.polygons):
            cands.append(o)
    assert cands, "no modifier-free smooth static mesh found in %s" % bpy.data.filepath
    cands.sort(key=lambda o: len(o.data.vertices), reverse=True)
    return cands[0]


def _eval_mesh(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    oe = obj.evaluated_get(dg)
    me = oe.to_mesh()
    me.calc_loop_triangles()
    pts = [tuple(v.co) for v in me.vertices]
    counts = [p.loop_total for p in me.polygons]
    indices = [me.loops[i].vertex_index for i in range(len(me.loops))]
    me.corner_normals  # ensure computed
    corner_nrm = [tuple(cn.vector) for cn in me.corner_normals]
    all_smooth = bool(me.polygons) and all(p.use_smooth for p in me.polygons)
    data = {
        "points": pts,
        "counts": counts,
        "indices": indices,
        "corner_normals": corner_nrm,
        "n_verts": len(me.vertices),
        "n_faces": len(me.polygons),
        "n_corners": len(me.loops),
        "all_smooth": all_smooth,
    }
    oe.to_mesh_clear()
    return data


def _export(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_viewport = False
    obj.hide_render = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.usd_export(
        filepath=OUT,
        check_existing=False,
        selected_objects_only=True,
        export_animation=False,
        export_materials=True,
        export_normals=True,
        evaluation_mode="RENDER",
        generate_preview_surface=True,
        root_prim_path="/root",
    )


def _aclose(a, b, eps):
    return abs(float(a) - float(b)) <= eps


def _vclose(a, b, eps):
    return all(_aclose(x, y, eps) for x, y in zip(a, b))


def main():
    obj = _pick_target()
    assert not obj.modifiers, (
        "%s has modifiers %r -- pick a static mesh so evaluated==authored topology"
        % (obj.name, [m.type for m in obj.modifiers])
    )
    print("Target static mesh:", obj.name)
    src = _eval_mesh(obj)
    print("Authored topology: verts=%d faces=%d corners=%d all_smooth=%s"
          % (src["n_verts"], src["n_faces"], src["n_corners"], src["all_smooth"]))
    assert src["all_smooth"], "fixture must be fully smooth-shaded to prove smooth preservation"

    _export(obj)
    print("USD_EXPORT_PATH:", OUT)

    from pxr import Usd, UsdGeom, Vt

    stage = Usd.Stage.Open(OUT)
    meshes = [p for p in stage.Traverse() if p.GetTypeName() == "Mesh"]
    assert meshes, "exported stage has no UsdGeomMesh"
    # The selected object becomes the only Mesh prim.
    mesh = UsdGeom.Mesh(meshes[0])
    print("USD Mesh prim:", mesh.GetPath())

    # --- (1) points: count + value match the evaluated vertex positions ----------
    pts = mesh.GetPointsAttr().Get()
    assert pts is not None, "Mesh has no points"
    assert len(pts) == src["n_verts"], (
        "points count %d != authored vert count %d" % (len(pts), src["n_verts"]))
    mism = 0
    for i, (a, b) in enumerate(zip(pts, src["points"])):
        if not _vclose(a, b, POS_EPS):
            mism += 1
            if mism <= 5:
                print("  point[%d] usd=%s src=%s" % (i, tuple(a), b))
    assert mism == 0, "%d/%d points diverge beyond %g" % (mism, len(pts), POS_EPS)
    print("points: %d verts match within %g" % (len(pts), POS_EPS))

    # --- (2) faceVertexCounts == authored per-face loop totals --------------------
    counts = mesh.GetFaceVertexCountsAttr().Get()
    assert list(counts) == src["counts"], "faceVertexCounts diverge from authored topology"
    print("faceVertexCounts: %d faces match" % len(counts))

    # --- (3) faceVertexIndices == authored corner vertex indices ------------------
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    assert list(indices) == src["indices"], "faceVertexIndices diverge from authored topology"
    assert len(indices) == src["n_corners"], "corner count mismatch"
    print("faceVertexIndices: %d corners match" % len(indices))

    # --- (4) subdivisionScheme == "none" (no spurious subdivision) ----------------
    scheme = mesh.GetSubdivisionSchemeAttr().Get()
    assert str(scheme) == "none", "subdivisionScheme is %r, expected 'none'" % scheme
    print("subdivisionScheme: none")

    # --- (5) extent == local-space bbox of the points (float eps) -----------------
    ext = mesh.GetExtentAttr().Get()
    assert ext is not None and len(ext) == 2, "Mesh has no extent"
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
    bb_min = (min(xs), min(ys), min(zs))
    bb_max = (max(xs), max(ys), max(zs))
    assert _vclose(ext[0], bb_min, POS_EPS), "extent min %s != bbox min %s" % (tuple(ext[0]), bb_min)
    assert _vclose(ext[1], bb_max, POS_EPS), "extent max %s != bbox max %s" % (tuple(ext[1]), bb_max)
    print("extent: [%s, %s] matches points bbox" % (tuple(ext[0]), tuple(ext[1])))

    # --- (6) normals: faceVarying, per-corner, match authored, smooth preserved ---
    nattr = mesh.GetNormalsAttr()
    nrm = nattr.Get()
    assert nrm is not None, "Mesh has no authored normals"
    interp = mesh.GetNormalsInterpolation()
    assert interp == UsdGeom.Tokens.faceVarying, (
        "normals interpolation is %r, expected faceVarying" % interp)
    assert len(nrm) == src["n_corners"], (
        "normals count %d != corner count %d" % (len(nrm), src["n_corners"]))
    nmis = 0
    for i, (a, b) in enumerate(zip(nrm, src["corner_normals"])):
        if not _vclose(a, b, NRM_EPS):
            nmis += 1
            if nmis <= 5:
                print("  normal[%d] usd=%s src=%s" % (i, tuple(a), b))
    assert nmis == 0, "%d/%d corner normals diverge beyond %g" % (nmis, len(nrm), NRM_EPS)
    print("normals: %d faceVarying corner normals match authored" % len(nrm))

    # Smooth preservation: on a smooth mesh, the corners that share a vertex must carry
    # the SAME normal (a flat/faceted export would give each face its own face normal).
    by_vert = {}
    for corner, vidx in enumerate(indices):
        by_vert.setdefault(vidx, []).append(corner)
    checked = smooth_ok = 0
    for vidx, corners in by_vert.items():
        if len(corners) < 2:
            continue
        checked += 1
        n0 = nrm[corners[0]]
        if all(_vclose(nrm[c], n0, NRM_EPS) for c in corners[1:]):
            smooth_ok += 1
        if checked >= 200:
            break
    assert checked > 0, "no shared-vertex corners found to test smoothness"
    frac = smooth_ok / checked
    assert frac > 0.9, (
        "smooth shading NOT preserved: only %d/%d shared verts have unified corner normals "
        "(looks flat-shaded)" % (smooth_ok, checked))
    print("smooth normals preserved: %d/%d sampled shared verts unified" % (smooth_ok, checked))

    print("PASS: geometry preserved -- points/counts/indices/extent/normals round-trip the "
          "authored static mesh; subdivisionScheme=none; smooth shading intact")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("FAIL:", exc, file=sys.stderr)
        sys.exit(1)
