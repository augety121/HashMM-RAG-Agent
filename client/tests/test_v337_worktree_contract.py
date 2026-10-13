"""V337 managed-worktree static contracts complement the real Node Git tests."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text("utf-8")


def test_worktree_manager_has_fail_closed_removal_contract():
    source = _read("desktop/services/worktree-manager.js")
    removal = source[source.index("async function removeWorktree"):source.index("async function cleanupManagedWorktrees")]
    assert 'execFile("git"' in source
    assert 'spawn("git"' in source and "shell: false" in source
    assert '"DIRTY_WORKTREE"' in removal
    assert '"IGNORED_WORKTREE_FILES"' in removal
    assert '"UNANCHORED_COMMITS"' in removal
    assert '"ACTIVE_WORKTREE"' in removal
    assert '"PERMANENT_WORKTREE"' in removal
    assert '"--force"' not in removal


def test_worktree_destination_and_copy_bounds_are_deterministic():
    source = _read("desktop/services/worktree-manager.js")
    assert "MAX_PATCH_BYTES = 20 * 1024 * 1024" in source
    assert "MAX_COPY_FILES = 200" in source
    assert "MAX_COPY_TOTAL_BYTES = 50 * 1024 * 1024" in source
    assert '"MANAGED_ROOT_ESCAPE"' in source
    assert "lstatSync(source)" in source and "isSymbolicLink()" in source
    assert "copied_file_fingerprints" in source
    assert "sourceFp.sha256 !== expected.sha256" in source


def test_worktree_ipc_is_narrow_and_workspace_leased():
    main = _read("desktop/main.js")
    preload = _read("desktop/preload.js")
    for channel in (
        "git:worktree-list", "git:worktree-create", "git:worktree-activate",
        "git:worktree-branch", "git:worktree-permanent", "git:worktree-remove",
        "git:workspace-lease-acquire", "git:workspace-lease-release",
    ):
        assert channel in main
        assert channel in preload
    assert "_queueWorktree" in main
    assert "ACTIVE_WORKSPACE_TASK" in main
    assert "TERMINAL_BUSY" in main
    assert "saveConfig({ cuWorkdir: valid.path })" in main


def test_computer_use_releases_workspace_lease_in_finally():
    source = _read("frontend-next/lib/cu.ts")
    assert "gitWorkspaceLeaseAcquire" in source
    assert "finally" in source
    assert "gitWorkspaceLeaseRelease(lease.token)" in source
    cockpit = _read("frontend-next/components/desktop/CockpitAgent.tsx")
    assert "workspaceDir" in cockpit
    assert "setPlan(null)" in cockpit
    assert "gitInspect(workspaceDir || undefined)" in cockpit


def test_worktree_ui_and_release_pipeline_expose_real_safety_boundaries():
    panel = _read("frontend-next/components/desktop/WorktreePanel.tsx")
    build = _read("installer-native/build-all.bat")
    assert ".worktreeinclude" in panel
    assert "普通未跟踪文件不会复制" in panel
    assert "ignored 文件无安全副本" in panel
    assert "不会用 `--force` 删除" in panel
    assert "test_worktree_manager.js" in build
    assert "managed worktree tests" in build
