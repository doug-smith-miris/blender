"""Regression test for BL-MAT-MIX-RGB-LOG-IO-NAMESPACE.

PR #1 ("USD/MaterialX: map ShaderNodeMixRGB (legacy) to <mix>", commit
65567e34 on miris/mtlx-mix-rgb, merged into miris/integration) added a
`NODE_SHADER_MATERIALX_BEGIN` block to
`source/blender/nodes/shader/nodes/node_shader_mix_rgb.cc` that calls
`CLOG_WARN(LOG_IO_MATERIALX, ...)` from inside the macro expansion.

`LOG_IO_MATERIALX` is declared in
`source/blender/nodes/shader/materialx/node_parser.h` (line 17) inside
`namespace blender::nodes::materialx`. The `NODE_SHADER_MATERIALX_BEGIN`
macro, however, expands its class body inside the enclosing
`node_shader_mix_rgb_cc` namespace — a sibling, not a child, of
`materialx`. Unqualified `LOG_IO_MATERIALX` therefore fails name lookup
and breaks the miris/integration build:

    error: 'LOG_IO_MATERIALX' was not declared in this scope;
    did you mean 'blender::nodes::materialx::LOG_IO_MATERIALX'?

The diagnostic agent's standalone reproducer at
`pipeline-runs/0749265d-bfa3-4aea-8a7a-20e646d61aa1/repro_log_io_namespace.cc`
exhibits the identical lookup failure under clang; the variant in
`repro_fix_verify.cc` compiles cleanly with the `materialx::` qualifier.

Why this is a STRUCTURAL (source-text) test, not a runtime test:

  * The bug is a compile-time name-lookup failure. If the qualifier is
    missing, the translation unit fails to build and no binary is
    produced. There is no runtime "wrong output" to assert against —
    only a binary or no binary.
  * The mikassa MaterialX export that exercises the CLOG_WARN path
    requires a fully-linked Blender executable. As of this run, an
    UNRELATED pre-existing compile error in
    `source/blender/io/usd/intern/usd_writer_material.cc:1303`
    (a typo'd recursive call to `find_bsdf_node_in_tree` where
    `find_node_of_type_recursive` was meant) blocks the link step,
    so the end-to-end export check is gated behind that separate
    finding (proposed as a candidate follow-on mission).

This test guarantees the source-level fix is in place: it greps the one
known affected call site and confirms it uses `materialx::LOG_IO_MATERIALX`,
not the bare unqualified form. It also scans every other shader-node
translation unit OUTSIDE the `materialx/` subdirectory to catch any new
unqualified call site that future PRs might introduce — those would
re-break the build the same way.

Run as a plain Python script (no Blender needed):

    python3 source/blender/io/usd/tests/test_miris_bl_mat_mix_rgb_log_io_namespace.py
"""

import os
import re
import sys


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
)

MIX_RGB_PATH = os.path.join(
    REPO_ROOT,
    "source", "blender", "nodes", "shader", "nodes", "node_shader_mix_rgb.cc",
)

NODE_PARSER_HEADER = os.path.join(
    REPO_ROOT,
    "source", "blender", "nodes", "shader", "materialx", "node_parser.h",
)

# Files inside the materialx/ subdirectory are already in
# `namespace blender::nodes::materialx`, so unqualified
# `LOG_IO_MATERIALX` is fine there.
MATERIALX_SUBDIR = os.path.join(
    REPO_ROOT, "source", "blender", "nodes", "shader", "materialx",
)
SHADER_NODES_DIR = os.path.join(
    REPO_ROOT, "source", "blender", "nodes", "shader", "nodes",
)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_log_io_materialx_declared_in_materialx_namespace():
    """Sanity: confirm LOG_IO_MATERIALX is genuinely declared inside
    `namespace blender::nodes::materialx` and not elsewhere. If a future
    refactor moves it, this regression test must be retargeted."""
    src = read(NODE_PARSER_HEADER)
    assert "namespace blender::nodes::materialx" in src, (
        "node_parser.h no longer opens `namespace blender::nodes::materialx`; "
        "retarget this test (the qualifier expected at the call site may "
        "have changed)."
    )
    assert re.search(r"extern\s+CLG_LogRef\s*\*\s*LOG_IO_MATERIALX\s*;", src), (
        "LOG_IO_MATERIALX is no longer declared as `extern CLG_LogRef *` in "
        "node_parser.h; retarget this test."
    )


def test_mix_rgb_uses_qualified_log_ref():
    """The fix: the one out-of-namespace call site must qualify
    LOG_IO_MATERIALX with `materialx::`. Without the qualifier the file
    fails to compile because the macro expands the class body into the
    sibling `node_shader_mix_rgb_cc` namespace, where the name isn't
    visible."""
    src = read(MIX_RGB_PATH)

    # The qualified form must be present.
    assert "materialx::LOG_IO_MATERIALX" in src, (
        f"REGRESSION (BL-MAT-MIX-RGB-LOG-IO-NAMESPACE): "
        f"{MIX_RGB_PATH} no longer references `materialx::LOG_IO_MATERIALX`. "
        f"The NODE_SHADER_MATERIALX_BEGIN block expands inside "
        f"`namespace node_shader_mix_rgb_cc`, a sibling of `materialx`, so "
        f"the qualifier is required for name lookup to succeed. Without "
        f"it the file fails to compile."
    )

    # The unqualified form (as a bare CLOG_WARN argument) must NOT be present.
    # We look for `CLOG_WARN(LOG_IO_MATERIALX` or `CLOG_DEBUG(LOG_IO_MATERIALX`
    # — i.e. a logger macro whose first argument is the bare symbol.
    unqualified_pat = re.compile(
        r"CLOG_(?:WARN|DEBUG|INFO|ERROR|FATAL)\s*\(\s*LOG_IO_MATERIALX\b"
    )
    m = unqualified_pat.search(src)
    assert m is None, (
        f"REGRESSION (BL-MAT-MIX-RGB-LOG-IO-NAMESPACE): {MIX_RGB_PATH} "
        f"contains an UNQUALIFIED `LOG_IO_MATERIALX` argument at offset "
        f"{m.start()} — name lookup will fail inside the macro-expanded "
        f"class body. Qualify it with `materialx::LOG_IO_MATERIALX`."
    )


def test_no_other_shader_node_uses_unqualified_log_ref():
    """Catch new offenders: any future node_shader_*.cc that adds an
    unqualified `LOG_IO_MATERIALX` inside a NODE_SHADER_MATERIALX_BEGIN
    block would re-break the build for the same namespace reason. Sweep
    everything under source/blender/nodes/shader/nodes/ and assert that
    every CLOG_*(LOG_IO_MATERIALX, ...) call site is `materialx::`-qualified."""
    offenders = []
    unqualified_pat = re.compile(
        r"CLOG_(?:WARN|DEBUG|INFO|ERROR|FATAL)\s*\(\s*LOG_IO_MATERIALX\b"
    )
    for name in os.listdir(SHADER_NODES_DIR):
        if not name.endswith(".cc"):
            continue
        path = os.path.join(SHADER_NODES_DIR, name)
        src = read(path)
        for m in unqualified_pat.finditer(src):
            offenders.append((path, m.start()))
    assert not offenders, (
        "REGRESSION (BL-MAT-MIX-RGB-LOG-IO-NAMESPACE): the following "
        "shader-node translation units use UNQUALIFIED `LOG_IO_MATERIALX` "
        "inside a CLOG macro — the NODE_SHADER_MATERIALX_BEGIN expansion "
        "lives in the per-node `*_cc` namespace, sibling to `materialx`, "
        "so each of these will fail name lookup and break the build:\n"
        + "\n".join(f"  {p} (offset {o})" for p, o in offenders)
        + "\nQualify each with `materialx::LOG_IO_MATERIALX`."
    )


def test_materialx_subdir_unqualified_usage_unchanged():
    """Inside source/blender/nodes/shader/materialx/, the translation
    units already live in `namespace blender::nodes::materialx`, so the
    unqualified form is correct there. This test is a sanity check that
    the fix did not over-rewrite files inside that subdir."""
    found_unqualified_in_materialx = False
    unqualified_pat = re.compile(
        r"CLOG_(?:WARN|DEBUG|INFO|ERROR|FATAL)\s*\(\s*LOG_IO_MATERIALX\b"
    )
    for root, _dirs, files in os.walk(MATERIALX_SUBDIR):
        for name in files:
            if not name.endswith((".cc", ".cpp", ".h", ".hh")):
                continue
            path = os.path.join(root, name)
            src = read(path)
            if unqualified_pat.search(src):
                found_unqualified_in_materialx = True
                break
        if found_unqualified_in_materialx:
            break
    assert found_unqualified_in_materialx, (
        "Expected at least one unqualified `CLOG_*(LOG_IO_MATERIALX, ...)` "
        "inside source/blender/nodes/shader/materialx/ — these call sites "
        "are already in the materialx namespace and need no qualifier. "
        "If all of them were rewritten, the fix probably went too far."
    )


def main():
    tests = [
        test_log_io_materialx_declared_in_materialx_namespace,
        test_mix_rgb_uses_qualified_log_ref,
        test_no_other_shader_node_uses_unqualified_log_ref,
        test_materialx_subdir_unqualified_usage_unchanged,
    ]
    failures = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failures.append((t.__name__, str(e)))
            print(f"  FAIL  {t.__name__}: {e}")
    if failures:
        print(f"\n{len(failures)} of {len(tests)} tests failed", file=sys.stderr)
        sys.exit(1)
    print(f"\n{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
