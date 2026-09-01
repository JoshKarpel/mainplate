from __future__ import annotations

from typing import get_args

import pytest
from conftest import OFFERED
from conftest import Stand
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.settings import ThinkingEffort
from pydantic_ai.settings import ThinkingLevel

from mainplate.agent import Choice
from mainplate.agent import Endpoints
from mainplate.agent import agent_for
from mainplate.thinking import THINKING_CHOICES
from mainplate.thinking import THINKING_NAMES
from mainplate.thinking import UnknownThinking
from mainplate.thinking import name_of_thinking
from mainplate.thinking import thinking_named

INSTRUCTIONS = "Answer as a fixture would."


class Watching(FunctionModel):
    """
    A stand-in model that records the settings each request was handed.

    Asserting on `Agent.model_settings` would only say the agent was constructed with something.
    What is worth pinning is that the value survives the capability stack and reaches the request,
    since `StepwiseDurability` wraps every model this console builds.
    """

    def __init__(self) -> None:
        super().__init__(lambda messages, info: ModelResponse(parts=[TextPart("ok")]))
        self.seen: list[ModelSettings | None] = []

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.seen.append(model_settings)
        return await super().request(messages, model_settings, model_request_parameters)


class TestTheVocabulary:
    @pytest.mark.parametrize(("name", "level"), THINKING_CHOICES)
    def test_every_offered_name_survives_a_round_trip(self, name: str, level: ThinkingLevel | None) -> None:
        assert thinking_named(name) == level
        assert name_of_thinking(level) == name

    def test_a_name_nobody_offers_is_refused_with_the_ones_that_are(self) -> None:
        with pytest.raises(UnknownThinking, match="'ferocious' is not a thinking level"):
            thinking_named("ferocious")

    def test_the_efforts_are_whatever_pydantic_ai_says_they_are(self) -> None:
        """
        The picker's efforts are recovered from the library's type rather than restated, so this
        fails the day one is added and the list here has not moved with it.
        """
        assert set(get_args(ThinkingEffort)) <= set(THINKING_NAMES)

    def test_saying_nothing_is_a_level_of_its_own_and_not_one_of_the_efforts(self) -> None:
        """
        Three requests that are genuinely different, and the reason the vocabulary is not just the
        efforts: no parameter at all, an explicit refusal to think, and the provider's own budget.
        """
        assert thinking_named("default") is None
        assert thinking_named("off") is False
        assert thinking_named("on") is True


class TestWhatAChoiceAsksOfAModel:
    def test_a_choice_that_says_nothing_about_thinking_asks_for_nothing(self) -> None:
        assert Choice(profile="here", model="ripe/careful").settings is None

    @pytest.mark.parametrize("level", [False, True, "minimal", "xhigh"])
    def test_a_chosen_level_becomes_the_one_setting(self, level: ThinkingLevel) -> None:
        assert Choice(profile="here", model="ripe/careful", thinking=level).settings == {"thinking": level}

    async def test_the_level_reaches_the_request_the_agent_makes(self) -> None:
        watcher = Watching()
        endpoints = Endpoints(by_profile={"here": Stand(offers=OFFERED["here"], responding=watcher)})
        chosen = Choice(profile="here", model="ripe/careful", thinking="xhigh")

        await agent_for(endpoints, chosen, INSTRUCTIONS).run("hello")

        assert watcher.seen == [{"thinking": "xhigh"}]

    async def test_a_session_that_chose_nothing_reaches_the_request_unchanged(self) -> None:
        """
        The case that must not regress: a session recorded before this setting existed builds an
        agent indistinguishable from the one it built then, rather than one asking for some level
        chosen on its behalf.
        """
        watcher = Watching()
        endpoints = Endpoints(by_profile={"here": Stand(offers=OFFERED["here"], responding=watcher)})

        await agent_for(endpoints, Choice(profile="here", model="ripe/careful"), INSTRUCTIONS).run("hello")

        assert watcher.seen == [None]
