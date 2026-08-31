# Where requests go and how they authenticate, as a file you edit rather than an environment.
#
# A **profile** is an endpoint and a credential. The model is not part of one, because the thing a
# profile names is often a gateway serving dozens of models: exe.dev's serves eighty-one behind one
# hostname, so folding the model in would mean a profile per model over identical settings. A
# session records both, separately, and both are fixed for its life.
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
from typing import Annotated
from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import ValidationError
from pydantic import model_validator

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


class Profile(BaseModel):
    """
    One endpoint and how to authenticate to it.

    Inbound, so nothing here is defaulted into existence: `provider` and `models` must be written
    down. `base_url` and `api_key` default to `None` because their *absence is the meaning* rather
    than an omission - no `base_url` is the provider's own endpoint, and no `api_key` is either a
    gateway that needs none or a fallback to the environment - and a parser cannot tell a
    forgotten optional field from an omitted one either way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["anthropic"]
    """Which SDK talks to this endpoint. One today; the field exists because the answer varies."""

    base_url: str | None = None
    """Where to send requests, or the provider's own endpoint when absent."""

    api_key: SecretStr | None = None
    """
    The credential, held redacted so it stays out of `repr()`, logs, and tracebacks.

    Absent means one of two things, decided by `base_url`: an endpoint of your own is assumed to
    authenticate its own callers, and the provider's own endpoint falls back to the environment.
    """

    models: Annotated[tuple[str, ...], Field(min_length=1)]
    """
    Which models this profile offers, in the order a picker should list them.

    At least one, because a profile nothing can be run on is a profile that cannot be chosen, and
    an empty list is how a half-finished edit renders rather than something anybody means.
    """

    @property
    def key(self) -> str | None:
        """
        What to hand the SDK: the configured key, a placeholder, or nothing at all.

        `None` is the case that keeps a Pydantic AI default intact, so a profile with neither a
        key nor an endpoint behaves exactly as `Agent('anthropic:...')` does and reads
        `ANTHROPIC_API_KEY` itself. The placeholder is for the opposite case, a gateway that
        authenticates at its edge and whose SDK still refuses to construct without something.

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

    @property
    def default_model(self) -> str:
        """The model a new session starts on, which is the default profile's first."""
        return self.profiles[self.default].models[0]

    def offers(self, profile: str, model: str) -> bool:
        """Whether this pair is still something a session can be answered on."""
        found = self.profiles.get(profile)
        return found is not None and model in found.models


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
    try:
        return Config.model_validate(document)
    except ValidationError as refused:
        raise BadConfig(str(refused)) from refused


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
