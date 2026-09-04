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
