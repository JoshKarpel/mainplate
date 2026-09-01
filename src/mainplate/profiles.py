# Where requests go and how they authenticate, as a file you edit rather than an environment.
#
# A **profile** is an endpoint, a wire format, and a credential. The models are not part of one and
# are not written down at all: a profile usually names a gateway serving dozens of models, so the
# list belongs to the endpoint rather than to the file, and `catalogue.py` asks the endpoint for it.
# What this file decides is where to ask. A session records a profile *and* a model, separately,
# and both are fixed for its life.
#
# `provider` is the wire format rather than the company: one hostname often answers both, and which
# one is spoken decides which models are reachable and what `base_url` has to say. exe.dev's gateway
# is the worked example - its Anthropic wire offers every Claude and every Fireworks model, its
# OpenAI wire offers GPT, Grok, and Fireworks again - so a VM that wants all of them declares the
# same host twice, once per wire.
#
# This is also where the credential stops being an environment variable. A key in a file this
# process reads and hands to `AnthropicProvider(api_key=...)` never enters the environment, so it
# is not inherited by children, not in `/proc/<pid>/environ`, and not in a crash dump of anything
# but this process. The environment is still read as a fallback, for a profile that names no key
# at all, because that is how Pydantic AI's own default behaves and how a machine installed before
# this file existed keeps working.

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import SecretStr
from pydantic import ValidationError
from pydantic import field_validator
from pydantic import model_validator

from mainplate.thinking import DEFAULT_THINKING
from mainplate.thinking import thinking_named

# What the Anthropic SDK is given when the endpoint authenticates by itself. It refuses to
# construct without a key, and a gateway that injects credentials at its edge has no use for one,
# so this is the value the exe.dev documentation itself suggests for exactly this case.
KEYLESS: Final[str] = "implicit"

CONFIG_NAME = "config.toml"


class BadConfig(ValueError):
    """
    The configuration file is not one this can run from, with the reason it is not.

    Raised where the file is read rather than where a profile is used, so a mistake in it is a
    failure at startup naming the file, instead of a session that cannot be answered discovered
    much later.
    """


type Wire = Literal["anthropic", "openai"]


class Profile(BaseModel):
    """
    One endpoint, the wire format spoken to it, and how to authenticate.

    Inbound, so nothing here is defaulted into existence: `provider` must be written down.
    `base_url` and `api_key` default to `None` because their *absence is the meaning* rather than
    an omission - no `base_url` is the provider's own endpoint, and no `api_key` is either a
    gateway that needs none or a fallback to the environment - and a parser cannot tell a
    forgotten optional field from an omitted one either way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Wire
    """
    Which SDK talks to this endpoint, which is a statement about the wire and not about the vendor.

    It decides three things at once, and they travel together: which models the endpoint will list,
    which of them it will actually answer for, and what `base_url` has to be. The Anthropic SDK
    appends `/v1/messages` to what it is given, so it wants the host; the OpenAI SDK appends
    `/chat/completions`, so it wants the host *and* `/v1`.
    """

    base_url: str | None = None
    """Where to send requests, or the provider's own endpoint when absent."""

    api_key: SecretStr | None = None
    """
    The credential, held redacted so it stays out of `repr()`, logs, and tracebacks.

    Absent means one of two things, decided by `base_url`: an endpoint of your own is assumed to
    authenticate its own callers, and the provider's own endpoint falls back to the environment.
    """

    @property
    def key(self) -> str | None:
        """
        What to hand the SDK: the configured key, a placeholder, or nothing at all.

        `None` is the case that keeps a Pydantic AI default intact, so a profile with neither a
        key nor an endpoint behaves exactly as `Agent('anthropic:...')` does and reads whichever
        variable its own SDK reads: `ANTHROPIC_API_KEY` on one wire, `OPENAI_API_KEY` on the other.
        The placeholder is for the opposite case, a gateway that authenticates at its edge and
        whose SDK still refuses to construct without something.

        It takes no environment, and that is the point rather than an omission: the environment
        fallback is the *SDK's*, reached by handing it nothing, so a copy of the environment read
        here would be a second implementation of a behaviour we are deliberately delegating.
        """
        if self.api_key is not None:
            return self.api_key.get_secret_value()
        if self.base_url is not None:
            return KEYLESS
        return None


class Config(BaseModel):
    """
    Every profile this console offers, and which one a new session starts on.

    Parsed once at startup into this, so everything downstream holds already-valid profiles and
    a name that is known to resolve.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: str
    profiles: Mapping[str, Profile]

    default_model: str | None = None
    """
    Which of the default profile's models a new session starts on, or its first when absent.

    A knob rather than a rule, because the answer moved when the models stopped being written
    down: "first" used to mean first in the file, which somebody chose, and now means first in
    whatever order an endpoint listed, which nobody did. Naming one here settles it; leaving it
    out accepts the endpoint's order, which is the sensible default for a gateway that puts its
    newest model first.

    Not checked against the endpoint here, because this is parsed before anything has been asked
    what it offers. `catalogue.py` is where a name that no longer resolves falls back to the first
    discovered, since a model retired overnight must not stop the console from starting.
    """

    default_thinking: str = DEFAULT_THINKING
    """
    How hard a new session thinks by default, as one of the names the picker offers.

    A name rather than the level itself, so the file reads the same as the control does and there
    is one spelling of "high" in the system. `"default"` means the request says nothing about
    thinking at all, which is what a model without this setting has always been asked.

    Unlike `default_model` this *is* checked here, because it can be: the levels are a closed set
    known before anything is discovered, so a typo is a startup failure naming the file rather than
    a picker quietly showing something else.
    """

    @field_validator("default_thinking")
    @classmethod
    def thinking_is_offered(cls, name: str) -> str:
        thinking_named(name)
        return name

    @model_validator(mode="after")
    def default_names_a_profile(self) -> Config:
        """
        The one cross-field rule, checked here so nothing downstream has to ask again.

        Without it `default` is a string that looks fine in the file and fails at the first
        attempt to start a session, which is both later and further from the mistake.
        """
        if not self.profiles:
            raise ValueError("there are no profiles, so there is nothing to talk to")
        if self.default not in self.profiles:
            offered = ", ".join(sorted(self.profiles))
            raise ValueError(f"default profile {self.default!r} is not one of: {offered}")
        return self


# A key that used to mean something and now means the opposite of what it says. `extra="forbid"`
# already refuses it, but "Extra inputs are not permitted" is the one message that reads as a typo
# when it is in fact a file written correctly against an older version of this program.
RETIRED: Final = "models"


def parse_config(raw: str) -> Config:
    """
    A configuration file's text as the profiles it declares, or a loud failure naming what is wrong.

    Pure, so the whole of what a file means is testable without one on disk, and so the one place
    that turns bytes into profiles is the one place that decides what a valid profile is.
    """
    try:
        document = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as broken:
        raise BadConfig(f"this is not valid TOML: {broken}") from broken
    named = declared_profiles(document)
    if any(RETIRED in profile for profile in named.values() if isinstance(profile, dict)):
        raise BadConfig(
            f"a profile still lists {RETIRED!r}, which is no longer read: the picker offers whatever "
            f"the endpoint says it serves. Delete the line, and name `default_model` at the top "
            f"level if you want a particular one to start selected."
        )
    try:
        return Config.model_validate(document)
    except ValidationError as refused:
        raise BadConfig(str(refused)) from refused


def declared_profiles(document: Mapping[str, object]) -> Mapping[str, object]:
    """The `[profiles.*]` tables as TOML produced them, before anything has decided they are valid."""
    named = document.get("profiles")
    return named if isinstance(named, dict) else {}


def read_config(path: Path) -> Config:
    """
    The profiles at `path`, or a loud failure naming the file.

    A missing file is a failure like any other rather than an empty default, because a console with
    no profiles cannot answer anything: starting anyway would mean a page that renders and refuses
    every message, which is the outcome `install` exists to prevent.
    """
    try:
        raw = path.read_text()
    except OSError as unreadable:
        raise BadConfig(f"cannot read {path}: {unreadable}") from unreadable
    try:
        return parse_config(raw)
    except BadConfig as bad:
        raise BadConfig(f"{path}: {bad}") from bad


def config_home(environ: Mapping[str, str]) -> Path:
    """Where configuration lives, by the XDG variable when it is set to something."""
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def config_path(config_home: Path) -> Path:
    return config_home / "mainplate" / CONFIG_NAME
