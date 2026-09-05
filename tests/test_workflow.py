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
