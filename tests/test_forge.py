from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from mainplate.exe import ExeDevGitHub
from mainplate.exe import parse_repositories
from mainplate.forge import Reachable
from mainplate.forge import Repository
from mainplate.forge import discover
from mainplate.forge import named

# The shape exe.dev's own documentation publishes, kept verbatim so a change to it is a failure
# here rather than a picker that quietly offers nothing.
REFLECTION = {
    "integrations": [
        {"comment": "", "help": "curl https://reflection.int.exe.xyz/", "name": "reflection", "type": "reflection"},
        {"comment": "", "help": "curl https://llm.int.exe.xyz/v1/models", "name": "llm", "type": "llm"},
        {
            "comment": "Use this for the backend repo; the app repo is at github-app.",
            "help": "git clone https://github.int.exe.xyz/your-org/your-repo.git",
            "name": "github-example",
            "type": "github",
        },
    ]
}


def document(**overrides: object) -> bytes:
    return json.dumps({**REFLECTION, **overrides}).encode()


class TestReadingWhatAVmReaches:
    def test_a_github_integration_is_a_repository(self) -> None:
        assert parse_repositories(document()) == (
            Repository(
                forge="exe-github",
                key="github-example",
                name="your-org/your-repo",
                # The integration's *own* host, not the aggregate one its help prints.
                url="https://github-example.int.exe.xyz/your-org/your-repo.git",
            ),
        )

    def test_the_repository_comes_from_the_url_and_not_from_the_integration(self) -> None:
        """
        What somebody called the attachment says nothing about which repository is behind it: the
        published example is named `github-example` and serves `your-org/your-repo`.
        """
        (found,) = parse_repositories(document())

        assert found.key == "github-example"
        assert found.name == "your-org/your-repo"

    def test_the_clone_url_is_rebuilt_on_the_integration_s_own_host(self) -> None:
        """
        `help` prints `github.int.exe.xyz`, which resolves to whichever attachment happens to serve
        that repository. The integration's own host resolves to exactly one, which is what tells an
        as-user attachment from an as-app one and what stops either reaching the other's repository.
        """
        (found,) = parse_repositories(document())

        assert found.url.startswith("https://github-example.int.exe.xyz/")
        assert "github.int.exe.xyz" not in found.url

    def test_an_id_names_the_forge_as_well_as_the_attachment(self) -> None:
        """Two forges can reach the same attachment name, so an id carrying only it is ambiguous."""
        (found,) = parse_repositories(document())

        assert found.id == "exe-github:github-example"

    def test_one_repository_attached_twice_is_two_things_to_pick_from(self) -> None:
        """
        The case the whole `key`/`name` split exists for: the same repository reachable as your
        user and as the app is two different things to start a session on.
        """
        raw = document(
            integrations=[
                {
                    "name": "repo-as-user",
                    "type": "github",
                    "comment": "",
                    "help": "git clone https://github.int.exe.xyz/o/r.git",
                },
                {
                    "name": "repo-as-app",
                    "type": "github",
                    "comment": "",
                    "help": "git clone https://github.int.exe.xyz/o/r.git",
                },
            ]
        )

        found = parse_repositories(raw)

        assert [each.id for each in found] == ["exe-github:repo-as-user", "exe-github:repo-as-app"]
        assert {each.name for each in found} == {"o/r"}, "the same repository, reached two ways"
        assert len({each.url for each in found}) == 2, "and two distinct hosts to reach it through"

    def test_integrations_that_are_not_repositories_are_passed_over(self) -> None:
        assert len(parse_repositories(document())) == 1, "reflection and llm are not repositories"

    @pytest.mark.parametrize(
        ("why", "raw"),
        [
            ("not json at all", b"<html>nope</html>"),
            ("json that is not a document", b"[]"),
            ("a document with no integrations", b'{"name": "a-vm"}'),
            ("integrations that are not a list", b'{"integrations": "some"}'),
        ],
    )
    def test_a_document_this_cannot_read_is_nothing_rather_than_a_failure(self, why: str, raw: bytes) -> None:
        """Reflection describes a VM this process happens to be on; it is not ours to validate."""
        assert parse_repositories(raw) == ()

    @pytest.mark.parametrize(
        ("why", "help_text"),
        [
            ("no url at all", "ask an administrator"),
            ("a url that is not a clone", "see https://exe.dev/docs/integrations-github.md"),
            ("a path with no owner", "git clone https://github.int.exe.xyz/lonely.git"),
            ("a path with too many parts", "git clone https://github.int.exe.xyz/a/b/c.git"),
        ],
    )
    def test_an_entry_whose_help_carries_no_repository_is_skipped(self, why: str, help_text: str) -> None:
        raw = document(integrations=[{"name": "odd", "type": "github", "help": help_text, "comment": ""}])

        assert parse_repositories(raw) == ()

    def test_help_that_words_itself_differently_still_parses(self) -> None:
        """Anchored on the URL rather than on the words around it, which exe.dev may reword."""
        raw = document(
            integrations=[
                {
                    "name": "reworded",
                    "type": "github",
                    "help": "Clone it: `https://github.int.exe.xyz/o/r.git` and off you go",
                    "comment": "",
                }
            ]
        )

        assert parse_repositories(raw) == (
            Repository(forge="exe-github", key="reworded", name="o/r", url="https://reworded.int.exe.xyz/o/r.git"),
        )


@dataclass(frozen=True, slots=True)
class Stand:
    """A stand-in forge, which is all it takes to be one: a name and what it reaches."""

    name: str
    reaches: tuple[Repository, ...] = ()
    broken: bool = False

    async def offers(self) -> tuple[Repository, ...]:
        if self.broken:
            raise RuntimeError("this forge is having a bad day")
        return self.reaches


ONE = Repository(forge="here", key="one", name="me/one", url="https://example.invalid/one.git")
TWO = Repository(forge="there", key="two", name="me/two", url="https://example.invalid/two.git")


class TestAskingEveryForge:
    async def test_what_every_forge_reaches_is_gathered(self) -> None:
        reachable = await discover([Stand(name="here", reaches=(ONE,)), Stand(name="there", reaches=(TWO,))])

        assert reachable.repositories == (ONE, TWO)

    async def test_a_forge_reaching_nothing_is_an_ordinary_answer(self) -> None:
        """
        Unlike an endpoint listing no models, which is an endpoint you can select and cannot use. A
        machine with no repositories attached is simply a machine with none.
        """
        reachable = await discover([Stand(name="here"), Stand(name="there", reaches=(TWO,))])

        assert reachable.repositories == (TWO,)

    async def test_a_forge_that_breaks_does_not_stop_the_others(self) -> None:
        """`offers` promises not to raise; one that does is its own mistake, not a failed start."""
        reachable = await discover([Stand(name="bad", broken=True), Stand(name="good", reaches=(ONE,))])

        assert reachable.repositories == (ONE,)

    async def test_no_forges_at_all_reaches_nothing(self) -> None:
        assert await discover([]) == Reachable(repositories=())


class TestOfferingOneBack:
    def test_a_posted_id_resolves_to_the_repository_it_names(self) -> None:
        assert Reachable(repositories=(ONE, TWO)).offers("there:two") == TWO

    def test_an_id_no_forge_reaches_is_nothing(self) -> None:
        """A select is a suggestion the page made, not a constraint on what can be posted."""
        assert Reachable(repositories=(ONE,)).offers("elsewhere:three") is None


class TestCallingOneByItsName:
    """The one rule a sidebar row and the note under a message box both ask, so neither can drift."""

    def test_a_repository_a_forge_reaches_is_called_what_a_person_calls_it(self) -> None:
        assert Reachable(repositories=(ONE, TWO)).readable("there:two") == "me/two"

    def test_one_nothing_reaches_is_called_by_the_id_the_session_recorded(self) -> None:
        """
        Not a fallback but the honest reading: an integration detached this morning does not move
        the session, and the id is all anybody knows about the repository now.
        """
        assert Reachable(repositories=(ONE,)).readable("elsewhere:three") == "elsewhere:three"


class TestNamingThemForAPicker:
    def test_a_repository_reached_once_is_called_what_it_is(self) -> None:
        assert Reachable(repositories=(ONE, TWO)).labelled() == ((ONE, "me/one"), (TWO, "me/two"))

    def test_a_repository_reached_twice_says_which_way(self) -> None:
        """Otherwise the picker has two identical rows and choosing between them is guessing."""
        as_user = Repository(forge="exe-github", key="repo-as-user", name="o/r", url="https://a.invalid/r.git")
        as_app = Repository(forge="exe-github", key="repo-as-app", name="o/r", url="https://b.invalid/r.git")

        assert Reachable(repositories=(as_user, as_app)).labelled() == (
            (as_user, "o/r (repo-as-user)"),
            (as_app, "o/r (repo-as-app)"),
        )

    def test_the_qualifier_appears_only_where_it_distinguishes_something(self) -> None:
        """A bare repository beside an ambiguous pair keeps its plain name."""
        as_user = Repository(forge="exe-github", key="repo-as-user", name="o/r", url="https://a.invalid/r.git")
        as_app = Repository(forge="exe-github", key="repo-as-app", name="o/r", url="https://b.invalid/r.git")

        labelled = dict(Reachable(repositories=(ONE, as_user, as_app)).labelled())

        assert labelled[ONE] == "me/one"
        assert labelled[as_user] == "o/r (repo-as-user)"


class TestReadingWhatBranchesARepositoryHas:
    """
    What `git ls-remote --heads` prints, as the completions the start page offers.

    The parsing is pure and pinned against the shape git actually produces; the call around it is
    exercised against a real repository in `test_snapshots.py`, since what it has to survive is a
    host that answers and one that does not.
    """

    def test_a_branch_is_whatever_follows_the_prefix(self) -> None:
        listed = "aaaa1111\trefs/heads/main\nbbbb2222\trefs/heads/release/2.1\n"
        assert named(listed) == ("main", "release/2.1")

    def test_a_name_with_slashes_in_it_is_kept_whole(self) -> None:
        """Cut once at the prefix rather than split on every separator, or `feature/a/b` is `a`."""
        assert named("cccc3333\trefs/heads/feature/deep/nested\n") == ("feature/deep/nested",)

    @pytest.mark.parametrize(
        "listed",
        [
            pytest.param("", id="a repository with no branches at all"),
            pytest.param("dddd4444\trefs/tags/v1\n", id="a tag, which this did not ask for"),
            pytest.param("ref: refs/heads/main\tHEAD\n", id="the symref line git prints for HEAD"),
            pytest.param("not a line git would print\n", id="anything else"),
        ],
    )
    def test_what_is_not_a_branch_contributes_nothing(self, listed: str) -> None:
        assert named(listed) == ()


class TestExeDevGitHub:
    async def test_it_answers_rather_than_raising_wherever_it_runs(self) -> None:
        """
        The property that keeps this console runnable anywhere, and the one thing about the live
        forge that can be asserted in both places it runs. On a VM this reaches whatever is
        attached; anywhere else the hostname does not resolve and the answer is nothing. Neither is
        a failure, which is the whole contract `offers` makes.

        What the entries *mean* is pinned above, against exe.dev's own published document, because
        that is a test that says the same thing on every machine.
        """
        reached = await ExeDevGitHub().offers()

        assert all(found.forge == "exe-github" for found in reached)
        assert all(found.url.endswith(".git") for found in reached)
        assert all(found.name.count("/") == 1 for found in reached)

    def test_it_names_itself_the_way_a_recorded_id_does(self) -> None:
        assert ExeDevGitHub().name == "exe-github"
