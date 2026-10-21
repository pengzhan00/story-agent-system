from __future__ import annotations

from dataclasses import replace

import pytest

from storyagent.config import Settings, load_settings
from storyagent.domain import MediaKind
from storyagent.domain.errors import WaitReason
from storyagent.media.broker import CaffeinateGuard, can_start, select_next
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger


@pytest.fixture
def led(fdb, fsettings) -> Ledger:  # noqa: ANN001
    return Ledger(fdb, fsettings)


def rec(led: Ledger, model: str, seed: int):  # noqa: ANN201
    r, _ = led.enqueue(MediaJob(MediaKind.VIDEO_GENERATE, {}, project_id=1, model_ref=model, seed=seed))
    return r


def test_one_heavy_at_a_time_but_light_may_run_beside_it(led: Ledger, fsettings: Settings) -> None:
    h1, h2, l1 = rec(led, "fake-heavy", 1), rec(led, "fake-heavy", 2), rec(led, "fake-video", 3)
    assert can_start(h1, [], fsettings).allowed
    gate = can_start(h2, [h1], fsettings)
    assert not gate.allowed and gate.reason is WaitReason.HEAVY_BUSY
    assert can_start(l1, [h1], fsettings).allowed


def test_light_concurrency_cap(led: Ledger, fsettings: Settings) -> None:
    s = replace(fsettings, runtime={**fsettings.runtime, "max_light_jobs": 2})
    lights = [rec(led, "fake-video", i) for i in range(3)]
    assert can_start(lights[2], lights[:1], s).allowed
    gate = can_start(lights[2], lights[:2], s)
    assert not gate.allowed and gate.reason is WaitReason.LIGHT_LIMIT


def test_memory_budget_blocks_but_a_lone_oversized_job_still_runs(led: Ledger, fsettings: Settings) -> None:
    s = replace(fsettings, runtime={**fsettings.runtime, "memory_budget_gib": 30, "max_light_jobs": 9})
    big = rec(led, "fake-heavy", 1)  # 40 GiB > budget
    small = rec(led, "fake-video", 2)  # 4 GiB
    assert can_start(big, [], s).allowed  # alone: allowed, otherwise it could never run
    gate = can_start(small, [big], s)
    assert not gate.allowed and gate.reason is WaitReason.MEMORY_BUDGET


def test_zero_budget_means_unlimited(led: Ledger, fsettings: Settings) -> None:
    s = replace(fsettings, runtime={**fsettings.runtime, "memory_budget_gib": 0, "max_light_jobs": 9})
    a = rec(led, "fake-heavy", 1)
    assert can_start(rec(led, "fake-video", 2), [a], s).allowed


@pytest.mark.parametrize(("policy", "heavy_allowed"), [("never", True), ("auto", True), ("ask", False)])
def test_llm_resident_policy(led: Ledger, fsettings: Settings, policy: str, heavy_allowed: bool) -> None:
    s = replace(fsettings, runtime={**fsettings.runtime, "llm_unload_policy": policy})
    heavy, light = rec(led, "fake-heavy", 1), rec(led, "fake-video", 2)
    gate = can_start(heavy, [], s, resident_llms=["gemma4:31b"])
    assert gate.allowed is heavy_allowed
    if not heavy_allowed:
        assert gate.reason is WaitReason.LLM_RESIDENT
    assert can_start(light, [], s, resident_llms=["gemma4:31b"]).allowed  # light jobs never wait for the LLM
    assert can_start(heavy, [], s, resident_llms=[]).allowed


def test_no_head_of_line_blocking(led: Ledger, fsettings: Settings) -> None:
    running = rec(led, "fake-heavy", 1)
    h2, l1 = rec(led, "fake-heavy", 2), rec(led, "fake-video", 3)
    chosen, waiting = select_next([h2, l1], [running], fsettings)
    assert chosen is not None and chosen.id == l1.id
    assert waiting == {h2.id: WaitReason.HEAVY_BUSY}
    assert select_next([h2], [running], fsettings)[0] is None


def test_caffeinate_guard_is_safe_when_disabled_and_cleans_up() -> None:
    with CaffeinateGuard(False) as g:
        assert not g.active
    with CaffeinateGuard(True) as g:  # real caffeinate on macOS, no-op elsewhere
        pass
    assert not g.active


def test_a_linear_queue_runs_one_local_job_at_a_time_whatever_its_kind(led: Ledger, fsettings: Settings) -> None:
    s = replace(fsettings, runtime={**fsettings.runtime, "max_concurrent_jobs": 1})
    heavy, light = rec(led, "fake-heavy", 1), rec(led, "fake-video", 2)
    assert can_start(heavy, [], s).allowed and can_start(light, [], s).allowed
    for running, waiting in (([heavy], light), ([light], heavy), ([light], rec(led, "fake-video", 3))):
        gate = can_start(waiting, running, s)
        assert not gate.allowed and gate.reason is WaitReason.QUEUE_LIMIT


def test_default_config_is_a_linear_queue(tmp_path) -> None:  # noqa: ANN001
    assert load_settings({"STORY_AGENT_HOME": str(tmp_path)}).runtime["max_concurrent_jobs"] == 1


def test_default_config_has_sane_scheduling_values(tmp_path) -> None:  # noqa: ANN001
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path)})
    assert s.runtime["max_light_jobs"] >= 1 and s.runtime["memory_budget_gib"] > 0
    assert s.runtime["heartbeat_seconds"] < s.runtime["lease_seconds"]


def test_ollama_lets_go_of_the_model_soon_after_our_last_call(tmp_path) -> None:  # noqa: ANN001
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path)})
    assert s.llm_backends["ollama"].options["keep_alive"] == "30s"


VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                  1000000.
Pages active:                                1022746.
Pages inactive:                              500000.
Pages speculative:                           100000.
Pages throttled:                                   0.
Pages wired down:                             341613.
Pages purgeable:                              50000.
"""


def test_vm_stat_is_read_as_the_memory_the_machine_can_still_give() -> None:
    from storyagent.media.broker import parse_vm_stat

    assert round(parse_vm_stat(VM_STAT) or 0, 1) == round((1000000 + 500000 + 100000 + 50000) * 16384 / 2**30, 1)
    assert parse_vm_stat("nonsense") is None


def test_a_job_waits_while_other_apps_hold_the_memory_it_needs(led: Ledger, fsettings: Settings) -> None:
    big = led.enqueue(MediaJob(MediaKind.VIDEO_GENERATE, {}, project_id=1, model_ref="fake-heavy", seed=1))[0]
    assert big.est_memory_gib > 0
    gate = can_start(big, [], fsettings, available_gib=big.est_memory_gib + 1)          # room, but not the headroom
    assert not gate.allowed and gate.reason is WaitReason.SYSTEM_MEMORY
    assert can_start(big, [], fsettings, available_gib=big.est_memory_gib + 100).allowed
    assert can_start(big, [], fsettings, available_gib=None).allowed                   # unknown (not macOS): no check
