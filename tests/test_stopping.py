"""Exercise cooperative control input and matching completed-end file state through real passes."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from calling import calling
from conftest import CONFIG
from conftest import DEFAULT_CHOICE
from conftest import FIXTURE
from conftest import INSTRUCTIONS
from conftest import OFFERED
from conftest import Scripted
from conftest import Stand
from conftest import calls
from conftest import started
from pydantic_ai.messages import ModelMessage
from pydantic_ai.messages import ModelResponse
from pydantic_ai.messages import TextPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.models.function import FunctionModel
from test_commands import planted
from test_commands import settled
from test_conversation import pass_at
from without_asgi import ASGIApp

from mainplate import records
from mainplate.agent import Wires
from mainplate.commands import Commands
from mainplate.commands import Slot
from mainplate.conversation import Result
from mainplate.conversation import command_tree_key
from mainplate.conversation import conversing
from mainplate.conversation import end_forkable
from mainplate.conversation import ending_tree_key
from mainplate.conversation import latest_tree
from mainplate.conversation import messages_key
from mainplate.conversation import model_key
from mainplate.conversation import opening_tree_key
from mainplate.conversation import openings
from mainplate.conversation import parse_messages
from mainplate.conversation import posted_in
from mainplate.conversation import recorded_command
from mainplate.conversation import recorded_prompt
from mainplate.conversation import stopped_in
from mainplate.conversation import tree_key
from mainplate.durability import parse_snapshot
from mainplate.forge import Workspaces
from mainplate.service import Service


async def test_stop_finishes_a_recorded_tool_batch_without_another_request(
    service: Service, workspaces: Workspaces
) -> None:
    """A yielded turn replays its completed exchange, but Stop prevents paying for another one."""
    service = replace(service, workspaces=workspaces)
    session = await started(service, "make a file", replace(DEFAULT_CHOICE, repository=FIXTURE))
    scripted = Scripted(script=(calls(("create", {"path": "kept.txt", "content": "kept changes\n"})),))
    body = conversing(scripted.endpoints(), INSTRUCTIONS, workspaces, allowance=1)
    await pass_at(service, body, session.id)
    assert await service.stop(session.id, 0)

    await pass_at(service, body, session.id)

    recorded = await service.checkpointer.load(session.id)
    assert scripted.asked == 1
    assert model_key(0, 1) not in recorded
    assert messages_key(0) in recorded
    assert any(key.startswith("turn:0:tool:") for key in recorded)
    assert end_forkable(recorded)
    snapshot = parse_snapshot(recorded[ending_tree_key(0)])
    assert snapshot is not None
    checkout = workspaces.checkout(session.id, FIXTURE)
    assert (checkout.root / "kept.txt").read_text() == "kept changes\n"
    opening = parse_snapshot(recorded[tree_key(0, 0)])
    assert opening is not None
    assert snapshot.tree != opening.tree
    assert latest_tree(recorded) == recorded[ending_tree_key(0)]
    branch = await service.fork(session.id, at=1, chosen=DEFAULT_CHOICE)
    assert branch is not None
    branched = await service.checkpointer.load(branch.id)
    assert branched[opening_tree_key(1)] == recorded[ending_tree_key(0)]


async def test_a_delayed_stop_does_not_stop_the_next_turn(service: Service) -> None:
    """The inbox record targets the control's turn, not whichever turn reads it."""
    session = await started(service, "first question")
    scripted = Scripted(script=(ModelResponse(parts=[TextPart("first answer")]),))
    body = conversing(scripted.endpoints(), INSTRUCTIONS)
    await pass_at(service, body, session.id)
    assert await service.stop(session.id, 0)
    await service.say(session.id, "second question")

    await pass_at(service, body, session.id)

    recorded = await service.checkpointer.load(session.id)
    assert scripted.asked == 2
    assert len(openings(recorded)) == 2
    assert len(parse_messages(recorded[messages_key(1)])) == 2


async def test_a_command_records_its_ending_state_even_when_it_exits_nonzero(
    service: Service, workspaces: Workspaces
) -> None:
    """Exit status does not prove a command left files untouched."""
    commands = Commands(service.checkpointer)
    service = replace(service, workspaces=workspaces, commands=commands)
    session = await planted(service, workspaces, replace(DEFAULT_CHOICE, repository=FIXTURE))
    try:
        entry = await service.run(session, "printf 'command change\\n' > command.txt; exit 7")
        assert entry is not None
        result = await settled(service, session, entry)
        recorded = await service.checkpointer.load(session)
        assert result.status == 7
        snapshot = parse_snapshot(recorded[command_tree_key(entry)])
        assert snapshot is not None
        assert (workspaces.checkout(session, FIXTURE).root / "command.txt").read_text() == "command change\n"
    finally:
        await commands.aclose()


async def test_commands_wait_for_a_stopped_turn_to_capture_its_end(service: Service, workspaces: Workspaces) -> None:
    """A yielded pass releases its OS lock, not the unfinished turn's ownership."""
    commands = Commands(service.checkpointer)
    service = replace(service, workspaces=workspaces, commands=commands)
    session = await started(service, "make a file", replace(DEFAULT_CHOICE, repository=FIXTURE))
    scripted = Scripted(script=(calls(("create", {"path": "turn.txt", "content": "turn changes\n"})),))
    body = conversing(scripted.endpoints(), INSTRUCTIONS, workspaces, allowance=1)
    await pass_at(service, body, session.id)
    try:
        entry = await service.run(session.id, "printf 'command changes\\n' > after.txt")
        assert entry is not None
        await service.stop(session.id, 0)
        await pass_at(service, body, session.id)
        await settled(service, session.id, entry)
        recorded = await service.checkpointer.load(session.id)
        turn_end = parse_snapshot(recorded[ending_tree_key(0)])
        command_end = parse_snapshot(recorded[command_tree_key(entry)])
        assert turn_end is not None
        assert command_end is not None
        assert turn_end.tree != command_end.tree
        assert latest_tree(recorded) == recorded[command_tree_key(entry)]
        assert end_forkable(recorded)
    finally:
        await commands.aclose()


async def test_stop_arriving_during_a_final_response_is_accepted_without_another_request(service: Service) -> None:
    """A final answer has no later steering drain; its completed-exchange check must see Stop."""
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        entered.set()
        await finish.wait()
        return ModelResponse(parts=[TextPart("finished the current answer")])

    model = FunctionModel(respond)
    endpoints = Wires(by_endpoint={name: Stand(offers=OFFERED[name], responding=model) for name in CONFIG.endpoints})
    session = await started(service, "a slow question")
    task = asyncio.create_task(pass_at(service, conversing(endpoints, INSTRUCTIONS), session.id))
    await entered.wait()
    assert await service.stop(session.id, 0)
    finish.set()
    await task

    recorded = await service.checkpointer.load(session.id)
    assert stopped_in(recorded, 0)
    assert model_key(0, 1) not in recorded
    assert ending_tree_key(0) in recorded
    assert len(parse_messages(recorded[messages_key(0)])) == 2


async def test_a_second_command_cannot_run_inside_the_first_capture(service: Service, workspaces: Workspaces) -> None:
    """Command ownership spans the ending capture, not just process exit."""
    commands = Commands(service.checkpointer)
    service = replace(service, workspaces=workspaces, commands=commands)
    session = await planted(service, workspaces, replace(DEFAULT_CHOICE, repository=FIXTURE))
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    checkout = workspaces.checkout(session, FIXTURE)
    first = await service.checkpointer.append(session, recorded_command("first"))
    second = await service.checkpointer.append(session, recorded_command("second"))

    async def first_work(holding: bytearray) -> Result:
        first_started.set()
        await release_first.wait()
        (checkout.root / "ordered.txt").write_text("first\n")
        return Result(status=0, output="first done")

    async def second_work(holding: bytearray) -> Result:
        second_started.set()
        (checkout.root / "ordered.txt").write_text("second\n")
        return Result(status=0, output="second done")

    try:
        commands.scheduled(Slot(session, first.key), first_work, checkout)
        await first_started.wait()
        commands.scheduled(Slot(session, second.key), second_work, checkout)
        release_first.set()
        await settled(service, session, second.key)
        assert second_started.is_set()
        recorded = await service.checkpointer.load(session)
        first_end = parse_snapshot(recorded[command_tree_key(first.key)])
        second_end = parse_snapshot(recorded[command_tree_key(second.key)])
        assert first_end is not None
        assert second_end is not None
        assert first_end.tree != second_end.tree
    finally:
        await commands.aclose()


async def test_live_end_and_stop_controls_follow_recorded_boundaries(service: Service, app: ASGIApp) -> None:
    """The served page offers a whole live end, and targets Stop to its opened turn."""
    session = await started(service, "first question")
    scripted = Scripted(script=(ModelResponse(parts=[TextPart("finished answer")]),))
    await pass_at(service, conversing(scripted.endpoints(), INSTRUCTIONS), session.id)
    async with calling(app) as caller:
        page = await caller.get(f"/sessions/{session.id}")
        assert page.status == 200
        assert f"/sessions/{session.id}/forks/new?at=1" in page.text
        form = await caller.get(f"/sessions/{session.id}/forks/new?at=1")
        assert form.status == 200
        await service.say(session.id, "next question")
        entry = await service.checkpointer.append(session.id, recorded_prompt("third question"))
        await service.checkpointer.supply(session.id, "turn:1:opened", entry.key)
        stop = await caller.post(f"/sessions/{session.id}/stop?at=1", {})
        assert stop.status == 303
        recorded = await service.checkpointer.load(session.id)
        assert any(isinstance(entry.what, records.Stop) and entry.what.turn == 1 for entry in posted_in(recorded))
