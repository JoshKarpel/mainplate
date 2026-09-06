from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelRequest
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import SystemPromptPart
from pydantic_ai.messages import ToolCallPart

from mainplate.guidance import GUIDANCE_DIRECTORY
from mainplate.guidance import approaching
from mainplate.guidance import console_guidance
from mainplate.guidance import described
from mainplate.guidance import guidance_at
from mainplate.guidance import guidance_under
from mainplate.guidance import indexing
from mainplate.guidance import instructing
from mainplate.guidance import readable
from mainplate.guidance import repository_guidance
from mainplate.guidance import without_frontmatter
from mainplate.snapshots import Worktree


async def run(*arguments: str, cwd: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        *arguments, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"{arguments} failed: {out.decode()}")
    return out.decode().strip()


def written(where: Path, name: str, said: str) -> Path:
    here = where / name
    here.parent.mkdir(parents=True, exist_ok=True)
    here.write_text(said, encoding="utf-8")
    return here


class TestTakingFrontmatterOff:
    def test_a_leading_yaml_block_goes_and_the_prose_stays(self) -> None:
        said = without_frontmatter('---\npaths:\n  - "**/*.py"\n---\n\n# Python\n\nAnnotate everything.\n')

        assert said == "# Python\n\nAnnotate everything.\n"

    def test_a_document_that_never_closes_its_block_is_left_alone(self) -> None:
        # Not frontmatter at all, whatever it looks like, and guessing would eat the whole document.
        said = "---\nthis is prose that opens with a rule\n"

        assert without_frontmatter(said) == said

    def test_a_document_with_no_block_is_left_alone(self) -> None:
        said = "# Naming\n\nSay what, not how.\n"

        assert without_frontmatter(said) == said

    def test_a_file_holding_only_frontmatter_says_nothing(self) -> None:
        assert without_frontmatter("---\npaths:\n  - '*'\n---\n") == ""

    def test_a_rule_of_dashes_further_down_is_not_a_closing_delimiter(self) -> None:
        # The block has to be the *opening* one, so a horizontal rule in the middle of a document
        # cannot close a block that was never opened.
        said = "# Tradeoffs\n\nPick an end.\n\n---\n\nAnd name what it costs.\n"

        assert without_frontmatter(said) == said


class TestWhatADirectoryIsToldBy:
    def test_agents_md_wins_over_claude_md(self, tmp_path: Path) -> None:
        # Both, deliberately: a repository carrying the pair is carrying one set of instructions
        # twice, and reading both would put it into the context window twice.
        written(tmp_path, "AGENTS.md", "the agents one")
        written(tmp_path, "CLAUDE.md", "the claude one")

        found = guidance_at(tmp_path)

        assert found is not None
        assert found.name == "AGENTS.md"

    def test_claude_md_answers_where_there_is_no_agents_md(self, tmp_path: Path) -> None:
        written(tmp_path, "CLAUDE.md", "the claude one")

        found = guidance_at(tmp_path)

        assert found is not None
        assert found.name == "CLAUDE.md"

    def test_a_directory_with_neither_says_nothing(self, tmp_path: Path) -> None:
        assert guidance_at(tmp_path) is None

    def test_a_directory_named_like_a_guidance_file_is_not_one(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").mkdir()

        assert guidance_at(tmp_path) is None


class TestReadingAFileThatWillNotRead:
    def test_bytes_that_are_not_text_say_nothing_rather_than_raising(self, tmp_path: Path) -> None:
        # A repository is free to commit anything under that name, and a session that could not be
        # answered because of it would be a console broken by somebody else's file.
        here = tmp_path / "AGENTS.md"
        here.write_bytes(b"\xff\xfe not utf-8 at all")

        assert readable(here) is None

    def test_a_path_that_is_not_there_says_nothing(self, tmp_path: Path) -> None:
        assert readable(tmp_path / "nowhere.md") is None


class TestWhatTheConsoleHasBeenTold:
    def test_every_file_loads_and_they_are_ordered_by_path(self, tmp_path: Path) -> None:
        # Sorted rather than however the filesystem answers, so two consoles reading one directory
        # compose the same instructions.
        where = tmp_path / "mainplate" / GUIDANCE_DIRECTORY
        written(where, "zebra.md", "last thing")
        written(where, "alpha.md", "first thing")

        assert console_guidance(tmp_path) == "first thing\n\nlast thing"

    def test_a_nested_directory_is_read_too(self, tmp_path: Path) -> None:
        where = tmp_path / "mainplate" / GUIDANCE_DIRECTORY
        written(where, "python/typing.md", "annotate it")

        assert console_guidance(tmp_path) == "annotate it"

    def test_frontmatter_is_taken_off_every_one_of_them(self, tmp_path: Path) -> None:
        where = tmp_path / "mainplate" / GUIDANCE_DIRECTORY
        written(where, "scoped.md", '---\npaths:\n  - "**/*.py"\n---\n\nannotate it\n')

        assert console_guidance(tmp_path) == "annotate it"

    def test_a_console_with_no_guidance_directory_says_nothing(self, tmp_path: Path) -> None:
        assert console_guidance(tmp_path) == ""

    def test_a_file_that_is_not_markdown_is_not_read(self, tmp_path: Path) -> None:
        where = tmp_path / "mainplate" / GUIDANCE_DIRECTORY
        written(where, "notes.txt", "not guidance")

        assert console_guidance(tmp_path) == ""


class TestWhatARepositorySaysAboutItself:
    def test_the_file_is_named_in_what_the_model_is_told(self, tmp_path: Path) -> None:
        # Named rather than left implicit, because the model can open the file and should know which
        # one it is already holding.
        written(tmp_path, "AGENTS.md", "# Polling\n\nRun `just test` first.\n")

        said = repository_guidance(tmp_path)

        assert said is not None
        assert said.startswith("`AGENTS.md`, this repository's own guidance:")
        assert "Run `just test` first." in said

    def test_frontmatter_is_taken_off_it_too(self, tmp_path: Path) -> None:
        written(tmp_path, "AGENTS.md", "---\ndescription: the web app\n---\n\nRun `just test` first.\n")

        said = repository_guidance(tmp_path)

        assert said is not None
        assert "description" not in said

    def test_a_repository_with_no_guidance_says_nothing(self, tmp_path: Path) -> None:
        assert repository_guidance(tmp_path) is None

    @pytest.mark.parametrize("held", ["", "   \n\n  \n"])
    def test_a_file_holding_nothing_worth_saying_says_nothing(self, tmp_path: Path, held: str) -> None:
        # An empty block would announce a heading and then nothing under it, which reads as guidance
        # that failed to arrive rather than as a repository that has none.
        written(tmp_path, "AGENTS.md", held)

        assert repository_guidance(tmp_path) is None


class TestWhatAGuidanceFileSaysItIsAbout:
    def test_a_description_in_frontmatter_is_what_the_index_prints(self) -> None:
        assert described("---\ndescription: the web app\n---\n\nbody\n") == "the web app"

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("# Web\n\nbody\n", id="no frontmatter at all"),
            pytest.param("---\npaths:\n  - '*'\n---\n\nbody\n", id="frontmatter without a description"),
            pytest.param("---\ndescription:\n---\n\nbody\n", id="a description holding nothing"),
            pytest.param("---\ndescription: ['a', 'list']\n---\n\nbody\n", id="a description that is not a string"),
            pytest.param("---\n: : not yaml : :\n---\n\nbody\n", id="frontmatter that will not parse"),
        ],
    )
    def test_anything_else_is_nothing_rather_than_a_failure(self, text: str) -> None:
        # A repository is free to put whatever it likes there, and a session that could not be
        # answered because of it would be a console broken by somebody else's file.
        assert described(text) is None


class TestTheIndexOfWhatElseTheRepositoryCarries:
    def test_every_file_is_one_row_and_a_description_rides_with_it(self, tmp_path: Path) -> None:
        written(tmp_path, "apps/web/AGENTS.md", "---\ndescription: the web app\n---\n\nbody\n")
        written(tmp_path, "apps/api/AGENTS.md", "# API\n\nbody\n")

        said = indexing(tmp_path, (Path("apps/web/AGENTS.md"), Path("apps/api/AGENTS.md")))

        assert said is not None
        assert "- `apps/web/AGENTS.md`: the web app" in said
        assert "- `apps/api/AGENTS.md`" in said, "and a file with no description is still listed"

    def test_the_rows_are_sorted_however_they_arrive(self, tmp_path: Path) -> None:
        written(tmp_path, "b/AGENTS.md", "body\n")
        written(tmp_path, "a/AGENTS.md", "body\n")

        said = indexing(tmp_path, (Path("b/AGENTS.md"), Path("a/AGENTS.md")))

        assert said is not None
        assert said.index("`a/AGENTS.md`") < said.index("`b/AGENTS.md`")

    def test_a_repository_carrying_none_gets_no_index_at_all(self, tmp_path: Path) -> None:
        # A heading over an empty list reports a feature rather than a fact, and most repositories
        # have exactly none of these.
        assert indexing(tmp_path, ()) is None


class TestFindingTheGuidanceUnderARepository:
    @pytest.fixture
    async def worktree(self, tmp_path: Path) -> Worktree:
        await run("git", "init", "-q", "-b", "main", cwd=tmp_path)
        return Worktree(root=tmp_path)

    async def test_a_nested_file_is_found_and_the_root_one_is_not(self, worktree: Worktree) -> None:
        # The root's own file is already in the instructions whole, so listing it again would be an
        # index whose first row points at what the reader has just been handed.
        written(worktree.root, "AGENTS.md", "the root's\n")
        written(worktree.root, "apps/web/AGENTS.md", "the web app's\n")

        assert await guidance_under(worktree) == (Path("apps/web/AGENTS.md"),)

    async def test_one_the_agent_has_only_just_written_is_found(self, worktree: Worktree) -> None:
        # `--others`, which is what makes a file the session created itself appear before anything
        # has committed it.
        written(worktree.root, "apps/web/AGENTS.md", "the web app's\n")

        assert await guidance_under(worktree) == (Path("apps/web/AGENTS.md"),)

    async def test_an_ignored_directory_is_not_walked_into(self, worktree: Worktree) -> None:
        # The reason this asks git rather than globbing: a `.venv` holds thousands of files and
        # sometimes one of these, and it is neither this repository's guidance nor anybody's.
        written(worktree.root, ".gitignore", ".venv/\n")
        written(worktree.root, ".venv/lib/somepackage/AGENTS.md", "somebody else's\n")

        assert await guidance_under(worktree) == ()

    async def test_a_directory_with_no_guidance_contributes_nothing(self, worktree: Worktree) -> None:
        written(worktree.root, "apps/web/main.py", "print('hi')\n")

        assert await guidance_under(worktree) == ()

    async def test_a_directory_holding_both_names_is_indexed_once(self, worktree: Worktree) -> None:
        # The pair a repository writes to wire one harness to the file every other harness reads.
        # Both match the pathspec, so without the first-name-wins rule the index carries a second row
        # per directory pointing at a file whose whole content is a line naming the first.
        written(worktree.root, "apps/web/AGENTS.md", "the web app's\n")
        written(worktree.root, "apps/web/CLAUDE.md", "@AGENTS.md\n")

        assert await guidance_under(worktree) == (Path("apps/web/AGENTS.md"),)

    async def test_a_directory_holding_only_the_second_name_is_still_found(self, worktree: Worktree) -> None:
        # The control on the rule above: first-name-wins must not become "only the first name".
        written(worktree.root, "apps/api/CLAUDE.md", "the api's\n")

        assert await guidance_under(worktree) == (Path("apps/api/CLAUDE.md"),)


def reaching(path: str, root: str | None = None) -> ModelResponse:
    """One model response calling `read` on a path, which is what an approach looks like."""
    arguments: dict[str, object] = {"path": path}
    if root is not None:
        arguments["root"] = root
    return ModelResponse(parts=[ToolCallPart(tool_name="read", args=arguments, tool_call_id="call-1")])


def handed(said: str) -> ModelRequest:
    """One block of guidance as the console delivers it, for asking what a later request sees."""
    return ModelRequest(parts=[SystemPromptPart(content=said)])


class TestHandingOverGuidanceWhereTheModelIsReaching:
    @pytest.fixture
    def repository(self, tmp_path: Path) -> Path:
        written(tmp_path, "AGENTS.md", "the root's\n")
        written(tmp_path, "apps/AGENTS.md", "everything under apps\n")
        written(tmp_path, "apps/web/AGENTS.md", "the web app's\n")
        written(tmp_path, "apps/web/src/main.py", "print('hi')\n")
        return tmp_path

    def test_reading_a_file_hands_over_what_covers_it(self, repository: Path) -> None:
        said = approaching(repository, [reaching("apps/web/src/main.py")])

        assert len(said) == 2, "every directory between the root and the file, and not the root"
        assert said[0].startswith("`apps/AGENTS.md`, guidance for this part of the repository:")
        assert "everything under apps" in said[0]
        assert "the web app's" in said[1]

    def test_the_root_is_never_handed_over_because_it_is_already_in_the_instructions(self, repository: Path) -> None:
        said = approaching(repository, [reaching("README.md")])

        assert said == ()

    def test_it_is_handed_over_once_and_not_again(self, repository: Path) -> None:
        # The history is the ledger, so what makes this work is that the delivery is *in* the
        # messages rather than in a set kept anywhere else.
        first = approaching(repository, [reaching("apps/web/src/main.py")])
        after: list[ModelMessage] = [
            reaching("apps/web/src/main.py"),
            *(handed(said) for said in first),
            reaching("apps/web/other.py"),
        ]

        assert approaching(repository, after) == ()

    def test_a_forget_hands_it_over_again(self, repository: Path) -> None:
        """
        The case a set on the session would get wrong, and get wrong silently.

        `reached` empties the history at a boundary, so after one the model has genuinely not been
        told: anything remembering that it *had* been would leave it working without guidance it can
        no longer see.
        """
        first = approaching(repository, [reaching("apps/web/src/main.py")])
        forgotten = [reaching("apps/web/src/main.py")]

        assert first, "the control: comparing two empty answers would pass with nothing working"
        assert approaching(repository, forgotten) == first

    def test_two_calls_in_one_batch_hand_it_over_once(self, repository: Path) -> None:
        batch = ModelResponse(
            parts=[
                ToolCallPart(tool_name="read", args={"path": "apps/web/a.py"}, tool_call_id="call-1"),
                ToolCallPart(tool_name="read", args={"path": "apps/web/b.py"}, tool_call_id="call-2"),
            ]
        )

        assert len(approaching(repository, [batch])) == 2, "apps and apps/web, rather than each twice"

    def test_a_path_in_another_root_reaches_nothing(self, repository: Path) -> None:
        # The only root that is not the worktree is the scratch, and nothing in there is the
        # repository's, so a call naming one is not an approach to anything.
        assert approaching(repository, [reaching("apps/web/notes.md", root="scratch")]) == ()

    def test_a_path_climbing_out_of_the_repository_reaches_nothing(self, repository: Path) -> None:
        # It cannot arrive from a tool, which refuses one long before this. It is read off what a
        # model wrote, so it is a value to answer rather than to crash on.
        assert approaching(repository, [reaching("../../etc/passwd")]) == ()

    def test_a_directory_holding_an_empty_guidance_file_hands_over_nothing(self, tmp_path: Path) -> None:
        written(tmp_path, "apps/web/AGENTS.md", "---\ndescription: nothing here\n---\n")
        written(tmp_path, "apps/web/main.py", "print('hi')\n")

        assert approaching(tmp_path, [reaching("apps/web/main.py")]) == ()

    def test_a_command_reaches_nothing_because_its_argv_is_not_ours_to_parse(self, repository: Path) -> None:
        # Stated rather than left to be discovered: this is the hole the index in the instructions
        # exists to cover, and the one a tree diff would close later.
        ran = ModelResponse(
            parts=[ToolCallPart(tool_name="bash", args={"command": "cat apps/web/main.py"}, tool_call_id="c")]
        )

        assert approaching(repository, [ran]) == ()


class TestComposingWhatARequestCarries:
    def test_the_repository_comes_last_so_it_wins(self) -> None:
        # A repository is right about itself, which is the local-conventions rule one layer out.
        said = instructing("be concise", "the repository says be verbose")

        assert said == "be concise\n\nthe repository says be verbose"

    @pytest.mark.parametrize("empty", [None, "", "   \n "])
    def test_a_scope_with_nothing_to_say_adds_no_blank_lines(self, empty: str | None) -> None:
        # Otherwise a console with no guidance and a repository with none would pad the prompt with
        # separators around nothing, which is what a reader would see in the panel.
        assert instructing("be concise", empty, "and direct") == "be concise\n\nand direct"

    def test_nothing_at_all_composes_to_nothing(self) -> None:
        assert instructing(None, "", None) == ""
