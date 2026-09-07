# Where a plugin comes from, what it is called, and what a tier is allowed to claim.
#
# One mechanism and three places it may be declared. **Bundled** ships in this repository;
# **user** is named in `config.yaml` by whoever runs the console; **repository** is named in
# `.mainplate/mainplate.yaml` at the session's own worktree and runs only once granted.
#
# The three are a **union and never a merge**. Each plugin is declared in exactly one place and
# carries all of its settings from there, so nothing anywhere resolves one declaration against
# another: with a merge there would have to be a rule for whose value of a shared field wins, and any
# such rule lets a repository reach one field of something you enabled.
#
# **A plugin declares no name of its own.** The key it is installed under is the whole of the name,
# which is what makes two people's plugins installable side by side: both may call themselves
# `guidance`, and a rule that a declared name must match its key would mean editing one of their
# files to run both.

from __future__ import annotations

import logging
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import ValidationError

from mainplate.plugins.protocol import Answering
from mainplate.plugins.protocol import Declared
from mainplate.plugins.protocol import Described

logger = logging.getLogger(__name__)

BUNDLED_ROOT: Final = Path(__file__).parent / "bundled"
"""
Where the plugins this repository ships live, one executable apiece.

Beside the code that speaks to them rather than under `assets/`, because they are not served to a
browser and their being scripts is the whole point: what runs them is a pipe, so they are as
inspectable and as replaceable as anybody else's.
"""

REPOSITORY_FILE: Final = Path(".mainplate") / "mainplate.yaml"
"""
Where a repository declares its own plugins, relative to the session's worktree root.

A directory rather than a dotfile at the root, so a repository that grows a second thing to say to
this console has somewhere to put it without another top-level file.
"""

TOOL_PREFIX: Final = "repo_"
"""
What a repository's tool names are prefixed with, and what no other tier's may begin with.

**Not tidiness.** Unprefixed, a repository's plugin could collide with one of yours deliberately,
and if a collision refused then a repository could stop your session starting - which is the thing
every other rule here is built to prevent. Prefixing costs description tokens and a clumsier name in
the prefix, and buys a tier that cannot interfere with anything.
"""

LEADER_SEPARATOR: Final = ":"
"""
What stands between a repository plugin's name and its leader, and what no other tier's may contain.

The same argument as `TOOL_PREFIX`, in the other namespace a contribution lands in: `/guidance` is a
word somebody types and can only mean one thing, so a repository's is `/review:lint` and a
collision with an operator's `/review` is unrepresentable rather than refused.
"""


class Tier(Enum):
    """
    Who wrote a plugin, which is the only thing that differs between the three places one is declared.

    It is a *value* and not a level of precedence: nothing merges, so this decides two things and no
    others. Whether the plugin may run at all without a grant, and whether its contributions are
    prefixed.
    """

    BUNDLED = "bundled"
    USER = "user"
    REPOSITORY = "repository"


@dataclass(frozen=True, slots=True)
class Installed:
    """
    One plugin as somebody installed it: where it came from, what they called it, and what to run.

    `name` is the key in whichever mapping declared it and is the whole of the plugin's name, so two
    plugins that both think of themselves as `guidance` are `alice-guidance` and `bob-guidance`
    without either file being touched.

    `path` is the executable. It is not held to being one here, because a script that has lost its
    executable bit is a failure with a message from the operating system, which is a better sentence
    than anything this could compose.
    """

    tier: Tier
    name: str
    path: Path

    @property
    def qualified(self) -> str:
        """
        What this plugin is called where it has to be told from another of the same name.

        The tier and the key, which is the whole of a plugin's identity: a bundled `guidance`, an
        operator's `guidance` and a repository's `guidance` are three plugins, and all three can be
        on at once. It is what the settings blob is keyed by, so the three hold three sets of
        settings rather than one they take turns overwriting.
        """
        return f"{self.tier.value}:{self.name}"

    @property
    def confined(self) -> bool:
        """
        Whether this plugin runs behind the sandbox, which every repository's does and no other does.

        **Fixed and narrow, and never the session's own.** A session on `Filesystem.EVERYTHING` gets
        a `bash` that reaches `/`; a repository's plugin in that session still reaches the worktree
        it was handed and nothing else. The isolation a session picked is a decision about what the
        *model* may reach, and a plugin is not the model.
        """
        return self.tier is Tier.REPOSITORY

    def tool_named(self, declared: str) -> str:
        """The name the model sees for one of this plugin's tools, prefixed where the tier must be kept apart."""
        if self.tier is not Tier.REPOSITORY:
            return declared
        return f"{TOOL_PREFIX}{self.name}_{declared}"

    def leader_named(self, declared: str) -> str:
        """The leader somebody types for one of this plugin's answers, separated where the tier must be."""
        if self.tier is not Tier.REPOSITORY:
            return declared
        return f"{self.name}{LEADER_SEPARATOR}{declared}"


@dataclass(frozen=True, slots=True)
class Enrolled:
    """
    One plugin as a session recorded it: where it came from, and everything it declared.

    Recorded rather than re-read, which is `turn:0:tree:0`'s own shape: a fact about one session that
    could only be learned by doing the work, written once and replayed after. It is what makes
    registration once per session true across a restart, and it is what stops a plugin edited under a
    running conversation reaching it on any turn at all.
    """

    installed: Installed
    described: Described

    @property
    def qualified(self) -> str:
        return self.installed.qualified

    def tools(self) -> tuple[tuple[str, Declared], ...]:
        """Every tool this plugin contributes, under the name the model will call it by."""
        return tuple((self.installed.tool_named(each.name), each) for each in self.described.tools)

    def answers(self) -> tuple[tuple[str, Answering], ...]:
        """Every composer answer this plugin contributes, under the leader somebody will type."""
        return tuple((self.installed.leader_named(each.leader), each) for each in self.described.answers)


class BadDeclaration(ValueError):
    """
    A plugin declaration this console will not act on, with the reason it will not.

    Raised where the declaring file is read rather than where a plugin is run, so a mistake in a
    `config.yaml` names the file at startup instead of a session that fails much later. A
    repository's own file raises the same way and is caught by the pass, which is what turns it into
    a sentence on that session's page.
    """


class Declaring(BaseModel):
    """
    What a file declaring plugins may say, which for a repository is plugins and nothing else.

    **Not "does not today".** There is no field here for an endpoint, a credential, an isolation
    level or a reference database, so the escalation a repository might reach for is
    unrepresentable rather than checked.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plugins: Mapping[str, Path] = {}


def declared_in(raw: str, where: str) -> Mapping[str, Path]:
    """
    The plugins one YAML document declares, or a loud failure naming the file.

    `safe_load` rather than `load`, and that is a security boundary rather than a preference: full
    YAML can name Python types to construct, so reading a repository's file with it would make
    committing that file equivalent to running code - before any grant had been consulted.
    """
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as broken:
        raise BadDeclaration(f"{where} is not valid YAML: {broken}") from broken
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise BadDeclaration(f"{where} declares a mapping, not {type(document).__name__}")
    try:
        return Declaring.model_validate(document).plugins
    except ValidationError as refused:
        raise BadDeclaration(f"{where}: {refused}") from refused


def bundled(root: Path = BUNDLED_ROOT) -> tuple[Installed, ...]:
    """
    Every plugin this repository ships, named after the file it is.

    Discovered rather than listed, so adding one is a file and nothing else - which is the same
    claim the tiers below make about somebody else's. Sorted, so two consoles reading one directory
    enrol them in the same order and a settings step draws the same page twice.
    """
    if not root.is_dir():  # pragma: no cover - the directory ships with the package
        return ()
    return tuple(
        Installed(tier=Tier.BUNDLED, name=found.name, path=found)
        for found in sorted(root.iterdir())
        if found.is_file() and not found.name.startswith((".", "_"))
    )


def installed_by(tier: Tier, declared: Mapping[str, Path], relative_to: Path | None = None) -> tuple[Installed, ...]:
    """
    A declared mapping as the plugins it installs, with every path made absolute.

    `relative_to` is what a repository's file is read against, since a path in it names something in
    the repository and the repository is somewhere this console chose to put it. An operator's paths
    are theirs, and a relative one there is resolved against the process's directory, which is the
    ordinary meaning of a relative path in a file somebody edits by hand.
    """
    return tuple(
        Installed(tier=tier, name=name, path=(relative_to / path if relative_to is not None else path.expanduser()))
        for name, path in sorted(declared.items())
    )


def repository_plugins(worktree: Path) -> tuple[Installed, ...]:
    """
    What a repository declares about itself, read from the worktree at the moment it was planted.

    **Read once, at turn 0, and recorded.** Every pass after replays the record and reads no file.
    Without this a model writes a plugin on turn 4 and the console runs it on turn 5, which is a way
    to run code of the model's choosing.

    A path climbing out of the repository is refused rather than resolved, because a declaration
    naming `../../../bin/sh` is a repository asking to run something the grant was never about. That
    is the one check here, and it is enough: everything else the file can name is inside the tree the
    grant already covers.

    Nothing at all where the file is absent, which is most repositories, and a grant for one of those
    is inert without anything having been fetched to find that out.
    """
    found = worktree / REPOSITORY_FILE
    try:
        raw = found.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return ()
    declared = declared_in(raw, str(REPOSITORY_FILE))
    here = worktree.resolve()
    installed: list[Installed] = []
    for each in installed_by(Tier.REPOSITORY, declared, relative_to=worktree):
        if here not in each.path.resolve().parents:
            raise BadDeclaration(f"{REPOSITORY_FILE} declares {each.name!r} at {each.path}, which is outside the tree")
        installed.append(each)
    return tuple(installed)


ON: Final = True
"""
Whether a plugin a session has said nothing about runs, which is every plugin on the settings step.

**Every declared plugin comes on, in every tier, and nothing sets a default off.** There is no name
stack and nothing shadows anything: a bundled `guidance`, an operator's `guidance` and a
repository's `guidance` are three plugins, and a session that has been told nothing runs all three.
Installing a plugin is the decision, and this console does not second-guess it by turning something
else off out of view.

Turning one off is a switch on the settings step, and turning a whole tier off is a switch on that
tier's heading, which is one control that sets the switches under it rather than a second kind of
answer. So there is nothing here that can be off without a control showing it.
"""


class Collides(ValueError):
    """
    Two plugins that are on both want one name in a space the model or a person sees.

    Refused, naming both, and safe to refuse precisely because both are yours: the refusal holds up
    the first message rather than the settings step, so the screen still draws, still lists the two
    plugins, and still has the switch that fixes it.

    Never raised about a repository's plugin. Those are prefixed, so a collision there is
    unrepresentable, and one that somehow remains is dropped rather than allowed to stop a session.
    """


def tool_names(plugin: Enrolled) -> tuple[str, ...]:
    """Every tool name one plugin claims, which is the space the model sees one list of."""
    return tuple(named for named, _ in plugin.tools())


def leader_names(plugin: Enrolled) -> tuple[str, ...]:
    """Every leader one plugin claims, which is the space somebody types one word into."""
    return tuple(named for named, _ in plugin.answers())


def refuse_collisions(enrolled: Sequence[Enrolled]) -> None:
    """
    Refuse a set of running plugins that cannot agree on a tool name or a leader.

    Asked of what is actually *on*, so it is a question about a set somebody can see and change: the
    two claimants are both listed on the settings step with a switch apiece, and turning either off
    is what fixes it.

    The cost, stated: **two tiers, two rules.** The uniform alternative, prefixing every plugin's
    tools, was not taken because it renames `hand_off` to something worse in every session's prefix
    to solve a collision the operator can already see and fix.
    """
    unprefixed = [plugin for plugin in enrolled if plugin.installed.tier is not Tier.REPOSITORY]
    for space, claiming in (("tool", tool_names), ("leader", leader_names)):
        claimed: dict[str, str] = {}
        for plugin in unprefixed:
            for named in claiming(plugin):
                held = claimed.get(named)
                if held is not None:
                    raise Collides(f"{held} and {plugin.qualified} both contribute the {space} {named!r}")
                claimed[named] = plugin.qualified


def without_collisions(enrolled: Sequence[Enrolled]) -> tuple[Enrolled, ...]:
    """
    The running set with any repository contribution that still collides taken out of it.

    The last line of the guarantee that a repository cannot interfere with anything. Prefixing makes
    a collision unrepresentable in the ordinary case; this is what happens if one arrives anyway,
    because an operator installed a plugin whose tool happens to be spelled like a prefixed one.

    Dropped and logged rather than refused, since the alternative is a repository deciding whether
    your session starts.
    """
    refuse_collisions(enrolled)
    unprefixed = [plugin for plugin in enrolled if plugin.installed.tier is not Tier.REPOSITORY]
    claimed = {named for plugin in unprefixed for named in tool_names(plugin)}
    leading = {named for plugin in unprefixed for named in leader_names(plugin)}
    kept: list[Enrolled] = []
    for plugin in enrolled:
        if plugin.installed.tier is not Tier.REPOSITORY:
            kept.append(plugin)
            continue
        clashing = [named for named in tool_names(plugin) if named in claimed]
        clashing.extend(named for named in leader_names(plugin) if named in leading)
        if clashing:
            logger.warning(f"{plugin.qualified} is not run: it claims {', '.join(clashing)}, which is already taken")
            continue
        kept.append(plugin)
    return tuple(kept)


def grouped(enrolled: Iterable[Enrolled]) -> tuple[tuple[Tier, tuple[Enrolled, ...]], ...]:
    """
    Every enrolled plugin under the tier that declared it, in the order the settings step draws them.

    **By where a plugin came from rather than by what it does**, which is the whole point: the three
    are not equally trusted, and a reader deciding what to leave on is deciding about provenance.

    Every tier is returned, empty ones included, so the step is the same shape on every session and
    the flow can be described, learned and tested as one thing rather than as however many lists a
    repository happens to produce.
    """
    held = tuple(enrolled)
    return tuple((tier, tuple(each for each in held if each.installed.tier is tier)) for tier in Tier)
