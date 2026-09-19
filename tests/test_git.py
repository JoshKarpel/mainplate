from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import DEFAULT_CHOICE
from conftest import OFFERED
from conftest import Stand
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel

from mainplate.agent import Wires
from mainplate.agent import agent_for
from mainplate.sandbox import Filesystem
from mainplate.sandbox import Isolation
from mainplate.snapshots import Worktree
from mainplate.tools.git.tools import GitIndex
from mainplate.tools.git.tools import git_tools
from mainplate.tools.git.tools import guarded


async def staged(worktree: Worktree) -> set[str]:
    """The paths in the real index that differ from `HEAD`."""
    listed = await worktree.demand("diff", "--cached", "--name-only")
    return set(listed.splitlines())


class TestChangingTheIndex:
    async def test_stage_takes_paths_literally(self, worktree: Worktree) -> None:
        literal = worktree.root / "*.txt"
        other = worktree.root / "other.txt"
        literal.write_text("literal\n")
        other.write_text("other\n")

        said = await GitIndex(worktree).apply("stage", (literal.name,))

        assert said == "Staged changes selected by 1 path."
        assert await staged(worktree) == {literal.name}

    async def test_stage_tracked_leaves_untracked_files_out(self, worktree: Worktree) -> None:
        (worktree.root / "src" / "kept.txt").unlink()
        (worktree.root / ".gitignore").write_text(".env\nbuilt/\n*.tmp\n")
        (worktree.root / "new.txt").write_text("new\n")

        said = await GitIndex(worktree).apply("stage-tracked")

        assert said == "Staged every tracked change in the worktree."
        assert await staged(worktree) == {".gitignore", "src/kept.txt"}

    async def test_stage_all_takes_every_non_ignored_change(self, worktree: Worktree) -> None:
        (worktree.root / "src" / "kept.txt").unlink()
        (worktree.root / ".gitignore").write_text(".env\nbuilt/\n*.tmp\n")
        (worktree.root / "new.txt").write_text("new\n")
        (worktree.root / ".env").write_text("SECRET=changed\n")

        said = await GitIndex(worktree).apply("stage-all")

        assert said == "Staged every change in the worktree."
        assert await staged(worktree) == {".gitignore", "new.txt", "src/kept.txt"}

    async def test_intent_to_add_registers_a_path_without_staging_its_content(self, worktree: Worktree) -> None:
        proposed = worktree.root / "proposed.txt"
        proposed.write_text("not staged\n")

        said = await GitIndex(worktree).apply("intent-to-add", (proposed.name,))

        entry = await worktree.demand("ls-files", "--stage", "--", proposed.name)
        assert said == "Registered 1 path with intent to add."
        assert entry.endswith(f" 0\t{proposed.name}")
        assert "not staged" in await worktree.demand("diff", "--", proposed.name)


class TestRefusingAnUnboundedOperation:
    async def test_a_path_operation_needs_a_path(self, worktree: Worktree) -> None:
        with pytest.raises(ModelRetry, match="needs at least one path"):
            await guarded(GitIndex(worktree).apply("stage"))

    async def test_stage_tracked_refuses_paths_instead_of_ignoring_them(self, worktree: Worktree) -> None:
        with pytest.raises(ModelRetry, match="stage-tracked takes no paths"):
            await guarded(GitIndex(worktree).apply("stage-tracked", ("src/kept.txt",)))

    async def test_stage_all_refuses_paths_instead_of_ignoring_them(self, worktree: Worktree) -> None:
        with pytest.raises(ModelRetry, match="stage-all takes no paths"):
            await guarded(GitIndex(worktree).apply("stage-all", ("src/kept.txt",)))


class TestWhereTheToolIsOffered:
    async def test_a_worktree_session_gets_git_in_its_model_request(self, worktree: Worktree) -> None:
        seen: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.extend(tool.name for tool in info.function_tools)
            return ModelResponse(parts=[TextPart("done")])

        model = FunctionModel(respond)
        wires = Wires(
            by_endpoint={
                DEFAULT_CHOICE.endpoint: Stand(offers=OFFERED[DEFAULT_CHOICE.endpoint], responding=model),
            }
        )
        chosen = replace(
            DEFAULT_CHOICE,
            repository="test:fixture",
            isolation=Isolation(filesystem=Filesystem.WORKTREE),
        )

        await agent_for(wires, chosen, "instructions", worktree=worktree).run("hello")

        assert "git" in seen


class TestWhatTheToolsetOffers:
    def test_one_git_tool_exposes_only_the_closed_operations(self, worktree: Worktree) -> None:
        toolset = git_tools(worktree)
        schema = toolset.tools["git"].function_schema.json_schema

        assert set(toolset.tools) == {"git"}
        assert schema["$defs"]["Operation"]["enum"] == ["stage", "stage-tracked", "stage-all", "intent-to-add"]
        assert "command" not in schema["properties"]
        assert "options" not in schema["properties"]
