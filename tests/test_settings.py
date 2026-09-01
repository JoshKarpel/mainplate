from __future__ import annotations

from pathlib import Path

import pytest

from mainplate.settings import Settings


class TestWhereWorkspacesGo:
    """
    The workspace root is a place, not a place plus whatever directory the process is in.

    Everything below this runs `git` with a `cwd` of its own choosing - a clone is made from the
    clones root, a worktree is added from the repository - so a relative root is resolved by git
    against the wrong directory. The clone then lands at `workspaces/clones/workspaces/clones/...`,
    the worktree lands inside the repository, and the checks that make both idempotent look at the
    path that was asked for, never find it, and let every pass try again.

    Resolved here because this is where a configured path enters the process, so one absolute value
    cannot be got wrong by the next consumer.
    """

    def test_a_root_beside_a_relative_database_is_still_absolute(self, tmp_path: Path) -> None:
        # The default, rather than an odd case: a fresh checkout gets `mainplate.db` in the working
        # directory, and `just serve` and `just demo` both name one there too.
        with pytest.MonkeyPatch.context() as patching:
            patching.chdir(tmp_path)
            root = Settings(database=Path("mainplate.db")).workspace_root
        assert root.is_absolute()
        assert root == tmp_path.resolve() / "workspaces"

    def test_a_named_relative_root_is_resolved_too(self, tmp_path: Path) -> None:
        with pytest.MonkeyPatch.context() as patching:
            patching.chdir(tmp_path)
            root = Settings(database=Path("mainplate.db"), workspaces=Path("elsewhere")).workspace_root
        assert root == tmp_path.resolve() / "elsewhere"

    def test_a_named_absolute_root_is_where_it_says(self, tmp_path: Path) -> None:
        named = tmp_path / "somewhere" / "else"
        assert Settings(database=tmp_path / "mainplate.db", workspaces=named).workspace_root == named

    def test_the_root_still_sits_beside_the_database_it_was_derived_from(self, tmp_path: Path) -> None:
        # The property the resolving must not quietly change: two consoles on two databases keep
        # their own worktrees, because a checkpoint and the files it talks about are one session.
        database = tmp_path / "over" / "here" / "mainplate.db"
        assert Settings(database=database).workspace_root == database.parent / "workspaces"
