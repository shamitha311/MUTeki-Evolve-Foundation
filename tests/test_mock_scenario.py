import pytest

from app.models import TrustedTargetRegistry
from muteki_adapter.mock import MockMutekiAdapter
from orchestration.mock_scenario import run_three_round_scenario


@pytest.mark.asyncio
async def test_mock_adapter_returns_normalized_result_and_events() -> None:
    target, rounds = await run_three_round_scenario()
    assert target.runtime_reference.startswith("mock://")
    assert [round_data.result.solved for round_data in rounds] == [
        False,
        False,
        True,
    ]
    assert rounds[0].result.progress_signals == ["reconnaissance"]
    assert rounds[1].result.progress_signals == ["strong evidence"]
    assert rounds[2].score.progress_score == 100.0


@pytest.mark.asyncio
async def test_mock_behavior_is_deterministic() -> None:
    first = await run_three_round_scenario()
    second = await run_three_round_scenario()
    assert first == second


@pytest.mark.asyncio
async def test_mock_event_stream_is_ordered_and_run_scoped() -> None:
    target, _ = (await run_three_round_scenario())
    adapter = MockMutekiAdapter(
        TrustedTargetRegistry({target.id: target}), run_id="run-1"
    )
    # The fixture is the source of the strategy lineage; only the stream behavior
    # is under test here.
    from orchestration.mock_scenario import build_three_round_scenario

    _, rounds = build_three_round_scenario()
    await adapter.run_strategy(target, rounds[0].strategy)
    events = [event async for event in adapter.subscribe_events("run-1")]
    assert [event.sequence for event in events] == [1, 2, 3]
    other_events = [event async for event in adapter.subscribe_events("other")]
    assert other_events == []
