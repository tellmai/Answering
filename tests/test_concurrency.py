import asyncio
import json

import pytest

from webassistant.batch import BatchControl, BatchRunner, BatchStore, StatisticsError
from webassistant.models import Candidate, ExecutionOptions, PracticeOptions
from webassistant.mock_exam import PracticePlanner


def people(count=6):
    return [Candidate(i + 2, "黔江区", f"并发演示{i}", f"11010119900101{i:04}") for i in range(count)]


def test_limit_refill_and_person_failure_are_independent(tmp_path):
    store = BatchStore(tmp_path, people())
    running, maximum, visited = set(), [], []
    async def perform(candidate):
        running.add(candidate.row)
        maximum.append(len(running))
        visited.append(candidate.row)
        try:
            await asyncio.sleep(0.01 if candidate.row == 2 else 0.04)
            if candidate.row == 3:
                raise ValueError("模拟个人失败")
        finally:
            running.remove(candidate.row)
    counts = asyncio.run(BatchRunner(store, BatchControl()).run(perform, 2))
    assert max(maximum) == 2 and len(visited) == 6
    assert counts["completed"] == 5 and counts["failed"] == 1
    assert not running


@pytest.mark.parametrize("immediate", [False, True])
def test_stop_applies_to_all_active_people_and_keeps_pending(tmp_path, immediate):
    store, control = BatchStore(tmp_path, people()), BatchControl()
    entered, release = [], asyncio.Event()
    async def perform(candidate):
        entered.append(candidate.row)
        await release.wait()
    async def run():
        task = asyncio.create_task(BatchRunner(store, control).run(perform, 3))
        while len(entered) < 3:
            await asyncio.sleep(0.01)
        if immediate:
            control.immediate.set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            control.after_current.set()
            release.set()
            await task
    asyncio.run(run())
    assert len(entered) == 3
    assert [r.status for r in store.records] == (["stopped"] * 3 if immediate else ["completed"] * 3) + ["pending"] * 3
    data = json.loads((store.folder / "人员状态.json").read_text(encoding="utf-8"))
    assert [r["status"] for r in data["records"]] == [r.status for r in store.records]


def test_pause_blocks_refill_until_user_decides(tmp_path):
    store, control = BatchStore(tmp_path, people(3)), BatchControl()
    release, entered = asyncio.Event(), []
    async def perform(candidate):
        entered.append(candidate.row)
        await release.wait()
    async def run():
        task = asyncio.create_task(BatchRunner(store, control).run(perform, 2))
        while len(entered) < 2:
            await asyncio.sleep(0.01)
        control.paused.set()
        release.set()
        await asyncio.sleep(0.1)
        assert len(entered) == 2 and not task.done()
        control.paused.clear()
        await task
    asyncio.run(run())
    assert len(entered) == 3


def test_statistics_failure_cancels_siblings_without_starting_pending(tmp_path, monkeypatch):
    store = BatchStore(tmp_path, people(4))
    original = store.export
    entered = []
    def export():
        if any(r.status == "completed" for r in store.records):
            raise StatisticsError("模拟统计被占用")
        original()
    monkeypatch.setattr(store, "export", export)
    async def perform(candidate):
        entered.append(candidate.row)
        await asyncio.sleep(0.01 if candidate.row == 2 else 30)
    with pytest.raises(StatisticsError):
        asyncio.run(BatchRunner(store, BatchControl()).run(perform, 2))
    assert entered == [2, 3]
    assert [r.status for r in store.records] == ["completed", "stopped", "pending", "pending"]


@pytest.mark.parametrize("value", [0, 31, 1.5, True])
def test_invalid_limits_rejected(value):
    with pytest.raises(ValueError, match="并发"):
        ExecutionOptions(value).validate()


def test_inflight_score_reservations_are_balanced_and_released():
    planner = PracticePlanner(PracticeOptions(fastest=True, score_min=98, score_max=99))
    plans = [planner.reserve_plan() for _ in range(6)]
    assert [p.target_score for p in plans].count(98) == 3
    assert [p.target_score for p in plans].count(99) == 3
    for plan in plans:
        planner.release_plan(plan)
    assert not any(planner.reserved.values())
