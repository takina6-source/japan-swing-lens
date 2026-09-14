from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "update-dashboard.yml"


def _workflow() -> dict:
    # BaseLoader avoids YAML 1.1 treating the key ``on`` as a boolean.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_publish_job_requires_the_test_job():
    jobs = _workflow()["jobs"]
    assert jobs["analyze-and-publish"]["needs"] == "test"
    assert "actions/deploy-pages" not in str(jobs["test"])


def test_actions_runs_the_full_pytest_suite_before_analysis():
    jobs = _workflow()["jobs"]
    test_runs = [step.get("run") for step in jobs["test"]["steps"] if "run" in step]
    publish_runs = [step.get("run") for step in jobs["analyze-and-publish"]["steps"]
                    if "run" in step]
    assert "python -m pytest -q" in test_runs
    assert "python scripts/fixed_input_regression.py" in test_runs
    assert all("pytest" not in command for command in publish_runs)
    assert any("scripts/export_web.py --refresh" in command for command in publish_runs)


def test_publish_waits_for_fixed_input_regression_gate():
    jobs = _workflow()["jobs"]
    assert jobs["analyze-and-publish"]["needs"] == "test"
    regression = next(step for step in jobs["test"]["steps"]
                      if step.get("name") == "Verify fixed-input regression")
    assert regression["run"] == "python scripts/fixed_input_regression.py"


def test_committee_export_runs_after_analysis_without_blocking_main_publish():
    steps = _workflow()["jobs"]["analyze-and-publish"]["steps"]
    analysis_index = next(index for index, step in enumerate(steps)
                          if "scripts/export_web.py" in step.get("run", ""))
    committee_index = next(index for index, step in enumerate(steps)
                           if step.get("name") == "Export Investment Committee JSON")
    upload_index = next(index for index, step in enumerate(steps)
                        if str(step.get("uses", "")).startswith("actions/upload-pages-artifact"))
    committee = steps[committee_index]
    assert analysis_index < committee_index < upload_index
    assert committee["run"] == "python scripts/export_committee.py"
    assert committee["continue-on-error"] == "true"
    assert committee["env"]["COMMITTEE_SEED_URL"].endswith("/committee")


def test_phase2a_permissions_are_job_scoped_and_publication_is_independent():
    workflow = _workflow()
    jobs = workflow["jobs"]
    assert workflow["permissions"] == {}
    assert jobs["analyze-and-publish"]["permissions"] == {
        "contents": "read", "pages": "write", "id-token": "write"
    }
    shadow = jobs["phase2a-production-shadow"]
    assert shadow["permissions"] == {"contents": "write", "actions": "read"}
    assert jobs["analyze-and-publish"]["needs"] == "test"
    assert shadow["needs"] == "analyze-and-publish"
    assert "phase2a-production-shadow" not in str(jobs["analyze-and-publish"])


def test_phase2a_initialize_is_manual_only_and_never_publishes_what_changed():
    workflow = _workflow()
    shadow = workflow["jobs"]["phase2a-production-shadow"]
    initialize = next(step for step in shadow["steps"]
                      if step.get("name") == "Initialize approved cutover")
    gate = next(step for step in shadow["steps"]
                if step.get("name") == "Enforce initialize event gate")
    assert initialize["if"] == "inputs.phase2a_initialize"
    assert 'github.event_name' in gate["run"]
    assert 'workflow_dispatch' in gate["run"]
    assert "cutover-manifest-sha256" in initialize["run"]
    assert "public/dashboard/briefing/state" not in str(shadow)


def test_phase2a_is_paused_on_schedule_and_requires_explicit_manual_flag():
    workflow = _workflow()
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    shadow = workflow["jobs"]["phase2a-production-shadow"]
    assert inputs["phase2a_run"]["default"] == "false"
    assert "github.event_name == 'workflow_dispatch'" in shadow["if"]
    assert "inputs.phase2a_run" in shadow["if"]
    assert "schedule" not in shadow["if"]


def test_lightweight_changes_run_before_morning_brief_and_publish():
    steps = _workflow()["jobs"]["analyze-and-publish"]["steps"]
    simple = next(i for i, step in enumerate(steps)
                  if "scripts/export_simple_changes.py" in step.get("run", ""))
    brief = next(i for i, step in enumerate(steps)
                 if "scripts/export_briefing.py" in step.get("run", ""))
    upload = next(i for i, step in enumerate(steps)
                  if str(step.get("uses", "")).startswith("actions/upload-pages-artifact"))
    assert simple < brief < upload
    assert "--retention 30" in steps[simple]["run"]


def test_schedule_waits_until_yahoo_daily_prices_are_settled():
    schedule = _workflow()["on"]["schedule"]
    assert schedule == [{"cron": "30 21 * * 1-5"}]


def test_phase2a_uses_immutable_release_assets_without_latest_pointer():
    shadow = _workflow()["jobs"]["phase2a-production-shadow"]
    rendered = str(shadow)
    assert "phase2a-state-v1" in rendered
    assert "phase2a_release_seed.py select" in rendered
    assert "phase2a_release_seed.py extract" in rendered
    assert "latest pointer" not in rendered.lower()
    assert "--clobber" not in rendered
