"""Suma dnia z rozbiciem (posiłki + przekąski + stałe pozycje = weryfikator) i podsumowanie tygodnia."""

from __future__ import annotations

from dietbot.checker import verify_plan
from dietbot.report.markdown import render_plan
from dietbot.report.summary import METRICS, day_summaries, week_summaries


def test_rows_add_up_to_verifier_totals(repo, week_result):
    plan = week_result.plan
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    days = day_summaries(plan, report, repo.assumptions, repo.products, repo.dishes)
    assert len(days) == len(plan.days) * len(repo.assumptions.persons)
    for s in days.values():
        assert s.consistent, (s.day, s.person_id, s.rows_sum, s.total)
        kinds = [r.kind for r in s.rows]
        assert kinds.count("posilek") == len(repo.assumptions.day.meals) and kinds[-1] == "stale"
        snacks = [x for x in plan.snacks if x.day == s.day and x.person_id == s.person_id]
        assert kinds.count("przekaska") == len(snacks)


def test_week_summary_totals_and_averages(repo, week_result):
    plan = week_result.plan
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    weeks = week_summaries(plan, report, repo.assumptions, repo.products, repo.dishes)
    assert [w.person_id for w in weeks] == [p.id for p in repo.assumptions.persons]
    for w in weeks:
        person = repo.assumptions.person(w.person_id)
        for m in METRICS:
            assert abs(w.total[m] - sum(d.total[m] for d in w.days)) < 1e-6
            assert abs(w.average[m] - w.total[m] / len(w.days)) < 1e-6
            band = person.targets.band(m)
            assert w.week_band[m] == (band.min * len(w.days), band.max * len(w.days))
        assert w.days_ok == sum(1 for d in w.days if d.ok)
        if w.days_ok == len(w.days):  # każdy dzień w widełkach → średnia i suma tygodnia też
            assert set(w.average_states.values()) == {"ok"} and set(w.total_states.values()) == {"ok"}


def test_export_and_view_show_day_sums_and_week_summary(repo, week_result, tmp_path):
    plan = week_result.plan
    report = verify_plan(plan, repo.assumptions, repo.products, repo.dishes)
    md = render_plan(plan, repo.assumptions, repo.products, repo.dishes, report)
    assert md.count("**Razem dzień**") == len(plan.days)
    assert "## Podsumowanie tygodnia" in md and md.count("**Średnio dziennie**") == len(repo.assumptions.persons)
    assert "Dni w widełkach:" in md
