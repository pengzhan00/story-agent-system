"""Which plans can run is found by scanning the machine (and the chosen tools), not kept by hand."""
from __future__ import annotations

from storyagent.config import Settings
from storyagent.pipelines import needs_of, scan_plans


def test_a_plan_needs_its_listed_services_and_the_adapters_of_its_models(settings: Settings) -> None:
    s = settings
    plan = s.pipelines["vertical_drama"]
    needs = needs_of(s, "vertical_drama")
    assert set(plan.requires) <= set(needs)
    assert {f"adapter.{s.model(ref).provider}" for ref in plan.models.values()} <= set(needs)


def test_every_plan_is_usable_when_everything_it_needs_is_ok(settings: Settings) -> None:
    s = settings
    everything = {n: "ok" for p in s.pipelines for n in needs_of(s, p)}
    assert all(p.usable and not p.missing for p in scan_plans(s, everything))


def test_a_missing_or_stopped_piece_makes_only_the_plans_that_need_it_unusable(settings: Settings) -> None:
    s = settings
    states = {n: "ok" for p in s.pipelines for n in needs_of(s, p)}
    victim = next(n for n in needs_of(s, "vertical_drama") if n.startswith("tool."))
    states[victim] = "missing_tool"
    by_name = {p.name: p for p in scan_plans(s, states)}
    assert (victim, "missing_tool") in by_name["vertical_drama"].missing
    for name, scan in by_name.items():          # exactly the plans that need it are out
        assert scan.usable == (victim not in needs_of(s, name))


def test_choosing_tools_narrows_the_list_even_when_the_machine_has_more(settings: Settings) -> None:
    s = settings
    states = {n: "ok" for p in s.pipelines for n in needs_of(s, p)}
    narrowed = scan_plans(s, states, selected=["ollama"])
    assert not any(p.usable for p in narrowed)
    assert all(state == "not_selected" for p in narrowed for n, state in p.missing if not n.startswith("adapter."))
    have = {n for n in needs_of(s, "vertical_drama") if not n.startswith("adapter.")}
    chosen = {n.removeprefix("tool.") for n in have}
    assert {p.name: p for p in scan_plans(s, states, selected=chosen)}["vertical_drama"].usable


def test_an_entry_the_scan_did_not_see_counts_as_not_checked(settings: Settings) -> None:
    s = settings
    scan = {p.name: p for p in scan_plans(s, {})}["vertical_drama"]
    assert not scan.usable and all(state == "unknown" for _, state in scan.missing)
