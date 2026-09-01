# Where requests go and how they authenticate, as a file you edit rather than an environment.
#
# An **endpoint** is a URL, the API format spoken to it, and a credential. The models are not part of
# one and are not written down at all: an endpoint usually fronts a gateway serving dozens, so the
# list belongs to the endpoint rather than to the file, and `catalogue.py` asks the endpoint for it.
# What this file decides is where to ask. A session records an endpoint *and* a model, separately,
# and both are fixed for its life.
#
# `format` is the shape of the API rather than the company behind it, and the two come apart
# completely here. One hostname often answers two formats, and each reaches models the other does
# not; meanwhile the models behind either are from four different vendors. exe.dev's gateway is the
# worked example - its Anthropic-format API offers Claude and Fireworks, its OpenAI-format API
# offers GPT, Grok and Fireworks again - so a VM declares the same host twice, once per format.
#
# That is also why the **provider** of a model (`anthropic`, `fireworks`, `xai`) is not a level of
# this file's hierarchy and is not configured anywhere. It is discovered, it is a property of a
# model rather than of an endpoint, and it crosses formats: every Fireworks model on that gateway is
# reachable over both, under the same id. So the shape is `endpoint -> model`, with the provider a
# heading the picker groups by. See `catalogue.py`.
#
# This is also where the credential stops being an environment variable. A key in a file this
# process reads and hands to `AnthropicProvider(api_key=...)` never enters the environment, so it
# is not inherited by children, not in `/proc/<pid>/environ`, and not in a crash dump of anything
# but this process. The environment is still read as a fallback, for an endpoint that names no key
# at all, because that is how Pydantic AI's own default behaves.

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final
from typing import Literal

import yaml
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

CONFIG_NAME = "config.yaml"


class BadConfig(ValueError):
    """
    The configuration file is not one this can run from, with the reason it is not.

    Raised where the file is read rather than where an endpoint is used, so a mistake in it is a
    failure at startup naming the file, instead of a session that cannot be answered discovered
    much later.
    """


type Format = Literal["anthropic", "openai"]


class Endpoint(BaseModel):
    """
    One URL, the API format spoken to it, and how to authenticate.

    Inbound, so nothing here is defaulted into existence: `format` must be written down. `url` and
    `api_key` default to `None` because their *absence is the meaning* rather than an omission - no
    `url` is the SDK's own endpoint, and no `api_key` is either a gateway that needs none or a
    fallback to the environment - and a parser cannot tell a forgotten optional field from an
    omitted one either way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Format
    """
    Which API shape this endpoint speaks, which is a statement about the wire and not about a vendor.

    It decides three things at once, and they travel together: which models the endpoint will list,
    which of them it will actually answer for, and what `url` has to be. The Anthropic SDK appends
    `/v1/messages` to what it is given, so it wants the host; the OpenAI SDK appends
    `/chat/completions`, so it wants the host *and* `/v1`.

    Deliberately the same word `model_reference.format` uses, because it is the same question asked
    twice: what shape are the bytes at the other end.
    """

    url: str | None = None
    """Where to send requests, or the SDK's own endpoint when absent."""

    api_key: SecretStr | None = None
    """
    The credential, held redacted so it stays out of `repr()`, logs, and tracebacks.

    Absent means one of two things, decided by `url`: an endpoint of your own is assumed to
    authenticate its own callers, and the SDK's own endpoint falls back to the environment.
    """

    @property
    def key(self) -> str | None:
        """
        What to hand the SDK: the configured key, a placeholder, or nothing at all.

        `None` is the case that keeps a Pydantic AI default intact, so an endpoint with neither a
        key nor a URL behaves exactly as `Agent('anthropic:...')` does and reads whichever variable
        its own SDK reads: `ANTHROPIC_API_KEY` on one, `OPENAI_API_KEY` on the other. The
        placeholder is for the opposite case, a gateway that authenticates at its edge and whose SDK
        still refuses to construct without something.

        It takes no environment, and that is the point rather than an omission: the environment
        fallback is the *SDK's*, reached by handing it nothing, so a copy of the environment read
        here would be a second implementation of a behaviour we are deliberately delegating.
        """
        if self.api_key is not None:
            return self.api_key.get_secret_value()
        if self.url is not None:
            return KEYLESS
        return None


type ReferenceFormat = Literal["models.dev"]

# The one format there is, and the value a file gets by naming a source and nothing else. Written
# down rather than left implicit so the field can be read as a closed set: a second format is one
# more member here and one more arm where `reference.py` matches on it, and the type checker names
# every place that has to answer for it.
DEFAULT_REFERENCE_FORMAT: Final[ReferenceFormat] = "models.dev"


class ModelReference(BaseModel):
    """
    Where to look up what the endpoints do not publish, and how to read what comes back.

    Two fields rather than one string, because they answer different questions and only one of them
    is likely to stay settled. `source` is where the bytes are; `format` is what they mean. Today
    there is exactly one format, so naming it looks like ceremony - but a database whose shape is
    assumed rather than declared is one that cannot be swapped without a migration, and the whole
    point of this being configuration is that somebody can point it elsewhere.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    """
    A URL to fetch or a path to read, told apart by whether it has an `http` scheme.

    One field for both, because nothing downstream cares which it got: the bytes are parsed the same
    way either way. It is what lets a machine with no outbound access name a file it already has
    without a second setting to keep in step with this one.
    """

    format: ReferenceFormat = DEFAULT_REFERENCE_FORMAT
    """
    Which database this is, so its records can be read into the shape a card is drawn from.

    Defaulted rather than required, because a file naming a source and no format is asking for the
    obvious thing, and validated rather than trusted, because it is a closed set known before
    anything is fetched: a typo is a startup failure naming the file rather than a picker that
    quietly shows nothing.
    """


class Config(BaseModel):
    """
    Every endpoint this console offers, and which one a new session starts on.

    Parsed once at startup into this, so everything downstream holds already-valid endpoints and
    a name that is known to resolve.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: str
    endpoints: Mapping[str, Endpoint]

    default_model: str | None = None
    """
    Which of the default endpoint's models a new session starts on, or its first when absent.

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

    model_reference: ModelReference | None = None
    """
    Where to look up cost, context windows, and capabilities, or nothing at all to look nowhere.

    Absent by default, and absent is a real answer rather than an unset one: with no reference this
    process calls nobody but the endpoints it declares, which is what makes it usable on a machine
    with no outbound access and pointed at a private repository. A card then shows what the endpoint
    routes and says nothing it was never asked to find out.

    Configured, and the console reads the database before it is ready and re-reads it on a timer.
    A read that fails is never a startup failure: unlike an endpoint that cannot say what it serves,
    a reference that will not load costs a card some numbers and can cost it nothing else.
    """

    @field_validator("default_thinking")
    @classmethod
    def thinking_is_offered(cls, name: str) -> str:
        thinking_named(name)
        return name

    @model_validator(mode="after")
    def default_names_an_endpoint(self) -> Config:
        """
        The one cross-field rule, checked here so nothing downstream has to ask again.

        Without it `default` is a string that looks fine in the file and fails at the first
        attempt to start a session, which is both later and further from the mistake.
        """
        if not self.endpoints:
            raise ValueError("there are no endpoints, so there is nothing to talk to")
        if self.default not in self.endpoints:
            offered = ", ".join(sorted(self.endpoints))
            raise ValueError(f"default endpoint {self.default!r} is not one of: {offered}")
        return self


def parse_config(raw: str) -> Config:
    """
    A configuration file's text as the endpoints it declares, or a loud failure naming what is wrong.

    Pure, so the whole of what a file means is testable without one on disk, and so the one place
    that turns text into endpoints is the one place that decides what a valid endpoint is.

    `safe_load` rather than `load`, and that is a security boundary rather than a preference: full
    YAML can name Python types to construct, so loading a configuration file with it would make
    editing that file equivalent to running code.
    """
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as broken:
        raise BadConfig(f"this is not valid YAML: {broken}") from broken
    if document is None:
        raise BadConfig("this file is empty, so it declares no endpoints")
    if not isinstance(document, dict):
        raise BadConfig(f"a configuration file is a mapping, not {type(document).__name__}")
    try:
        return Config.model_validate(document)
    except ValidationError as refused:
        raise BadConfig(str(refused)) from refused


def read_config(path: Path) -> Config:
    """
    The endpoints at `path`, or a loud failure naming the file.

    A missing file is a failure like any other rather than an empty default, because a console with
    no endpoints cannot answer anything: starting anyway would mean a page that renders and refuses
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
