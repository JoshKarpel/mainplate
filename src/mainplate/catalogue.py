# What every profile currently offers, asked of the endpoints rather than read out of a file.
#
# This is the one piece of process state that changes under a reader, and it is worth being exact
# about why that does not contradict the checkpoint being the conversation. Nothing here is
# anything anybody said. A catalogue is configuration that happens to live at the far end of a
# request instead of on disk, and it is handled the way any reloadable configuration is: read once
# before the server is ready, refreshed on a timer by a task that answers no requests, and swapped
# in whole so that a reader holding one never sees half of the next.
#
# The refresh is a control plane and never sits on the request path. A page render reads the
# current value out of memory; it never causes a request to a gateway, so a slow provider cannot
# become a slow console, and the number of times the list is fetched is decided by the interval
# rather than by how many people are looking.
#
# A refresh that fails keeps the last good catalogue and says so. There is deliberately no
# staleness bound past which it gives up: the bound would have to be invented, the value it would
# discard is nearly static, and what it buys - a console that stops offering models because a
# gateway was unreachable for an hour - is worse than the staleness it prevents. What is *not*
# tolerated is a first read that fails, because a catalogue nobody has ever filled offers nothing.

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from mainplate.agent import Choice
from mainplate.agent import Endpoints
from mainplate.agent import Listed
from mainplate.profiles import Config
from mainplate.thinking import thinking_named

logger = logging.getLogger(__name__)


class NothingOffered(RuntimeError):
    """
    An endpoint could not say what it serves, at the one moment there is nothing to fall back on.

    Raised only from the read that happens before the server is ready, which makes it a startup
    failure of the same kind as an unparseable configuration file: a console whose picker is empty
    can answer nothing, so failing loudly beats serving a page that refuses every message.
    """


@dataclass(frozen=True, slots=True)
class Catalogue:
    """
    Every profile and what it offers, as one value replaced whole rather than edited in place.

    A value and not a place, which is what lets the console read it without coordinating with the
    task that refreshes it: whoever holds one holds a consistent answer for as long as they need
    it, even if a newer one lands mid-render.

    It carries the default choice as well as the offerings, because working the default out means
    knowing both what the file asked for and what the endpoint turned out to have. Deciding it once
    here is what keeps `pages` a pure function of one argument, and stops two callers deriving
    "the model a new session starts on" from the same two inputs in two slightly different ways.
    """

    offered: Mapping[str, tuple[Listed, ...]]
    default: Choice

    @property
    def profiles(self) -> tuple[str, ...]:
        """Every profile, in the order the picker lists them, which is the order somebody reads."""
        return tuple(sorted(self.offered))

    def models_of(self, profile: str) -> tuple[Listed, ...] | None:
        """What one profile offers, or nothing at all where there is no such profile."""
        return self.offered.get(profile)

    def offers(self, profile: str, model: str) -> bool:
        """
        Whether this pair is one the picker put in front of somebody.

        Asked when a *new* session is started, to check that a posted form names a pair the page
        actually offered. It is deliberately not what decides whether an existing session can be
        answered: this says what an endpoint advertises, which is narrower than what it will route,
        so a session already recorded on an unadvertised id is answerable and this would call it
        stuck. `models_of(...) is not None` is that other question, and it asks about the profile.
        """
        found = self.offered.get(profile)
        return found is not None and any(model == offered.id for offered in found)


async def discover(endpoints: Endpoints, config: Config) -> Catalogue:
    """
    Ask every endpoint what it serves, all at once, and refuse to return a catalogue with a hole.

    Concurrent because the profiles are independent and each is a round trip: a VM declaring the
    same gateway twice, once per wire, should wait for one list and not two.

    A profile that lists nothing is a failure rather than an empty entry. An empty entry renders as
    a profile you can select and then cannot use, which is the state hardest to diagnose from the
    page; and at startup it is the difference between a service that refuses to come up naming the
    endpoint and one that comes up unable to answer anything.
    """
    named = tuple(endpoints.by_profile)
    listings = await asyncio.gather(*(endpoints.by_profile[name].listed() for name in named), return_exceptions=True)
    offered: dict[str, tuple[Listed, ...]] = {}
    for name, listing in zip(named, listings, strict=True):
        if isinstance(listing, BaseException):
            raise NothingOffered(f"profile {name!r} could not be asked what it serves: {listing!r}") from listing
        if not listing:
            raise NothingOffered(f"profile {name!r} says it serves no models")
        offered[name] = listing
    return Catalogue(offered=offered, default=default_choice(offered, config))


def default_choice(offered: Mapping[str, tuple[Listed, ...]], config: Config) -> Choice:
    """
    What a new session starts on: the configured profile, and the model the file named or the first.

    The name from the file is checked against what was actually discovered rather than trusted,
    because it is the one setting here that can be made wrong by somebody else: a model retired
    overnight would otherwise leave a picker whose selected option does not exist. Falling through
    to the first is a default and not a fallback - there is no second mechanism, only a value that
    was not supplied.

    The thinking level needs none of that care and gets none: it is a closed set checked when the
    file was parsed, so by here it is already a level rather than a name to be doubted.
    """
    thinking = thinking_named(config.default_thinking)
    models = offered[config.default]
    named = config.default_model
    if named is not None and any(named == model.id for model in models):
        return Choice(profile=config.default, model=named, thinking=thinking)
    if named is not None:
        logger.warning(f"default_model {named!r} is not offered by profile {config.default!r}, using {models[0].id!r}")
    return Choice(profile=config.default, model=models[0].id, thinking=thinking)


@dataclass(slots=True)
class Catalogues:
    """
    The current catalogue, and the only mutable thing in this process a request handler can see.

    A holder rather than the value itself, because the readers outlive any one answer: the console
    and the worker are handed this once at startup and go on reading it for the life of the
    process, while the refresher replaces what it holds. The field is rebound, never edited, so
    every value it has ever held is still a whole and consistent catalogue.
    """

    current: Catalogue


async def refreshing(holder: Catalogues, endpoints: Endpoints, config: Config, every: timedelta) -> None:
    """
    Re-ask every endpoint on a timer, for as long as this is running.

    Sleeps first, because the caller has already done the read that filled the holder: waking
    immediately would spend a round trip re-learning what was learned a moment ago. That ordering
    is deliberate and is not covered by a test, because distinguishing it from the other order
    needs control of the clock, and what it saves is one request at startup.

    A failed round is logged and dropped. That is the whole of the handling, and it is the case the
    module comment argues for: the previous catalogue is a good answer to the question that was
    asked, so the only thing a failure changes is how old the answer is.
    """
    while True:
        await asyncio.sleep(every.total_seconds())
        try:
            holder.current = await discover(endpoints, config)
        except NothingOffered as unanswered:
            logger.warning(f"keeping the models discovered earlier: {unanswered}")
        else:
            logger.info(f"models refreshed: {summarise(holder.current)}")


def summarise(catalogue: Catalogue) -> str:
    """One line naming what was found, which is what a log is read for after a model appears."""
    return ", ".join(f"{name} offers {len(models)}" for name, models in sorted(catalogue.offered.items()))


def grouped(models: Sequence[Listed]) -> tuple[tuple[str, tuple[Listed, ...]], ...]:
    """
    One profile's models as the families it fronts, each keeping the order the endpoint gave.

    Grouping is what makes a list of seventy readable, and the endpoint's own order is what makes
    each group useful: a gateway lists its newest model first, and no ordering this could impose
    would know that. So the families are ordered by where each first appeared rather than
    alphabetically, which puts the vendor the endpoint leads with at the top.
    """
    families: dict[str, list[Listed]] = {}
    for model in models:
        families.setdefault(model.family, []).append(model)
    return tuple((family, tuple(found)) for family, found in families.items())
