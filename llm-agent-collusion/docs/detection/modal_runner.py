"""Optional CPU orchestration on Modal; never run during the offline build.

Install/configure Modal separately, and create a Secret named
``collusion-detection-provider`` containing OPENROUTER_API_KEY or NEBIUS_API_KEY.
Only explicitly invoking ``modal run`` builds this image or launches resources.
No GPU serving is declared. Full outputs persist in ``collusion-detection``.

From llm-agent-collusion/, after the documented local acceptance checks:

    modal run docs/detection/modal_runner.py \
        --config-path configs/detection/offline.json --episodes-path DATA.jsonl

For paid inference, first run local ``doctor`` and ``plan --dry-run`` for the
exact source/config/episodes, review the plan, then pass ``--live``,
``--acceptance-path RECEIPT.json`` and ``--plan-path PLAN.json``. The runner
validates these locally before dispatch. Remotely it repeats offline doctor,
regenerates a zero-request plan and verifies equality before hosted validation
and paid completions. No local credentials or compute.env are mounted.

API references checked during implementation:
https://modal.com/docs/sdk/py/latest/Image
https://modal.com/docs/guide/volumes
https://modal.com/docs/guide/apps
"""

from pathlib import Path

try:
    import modal
except ImportError as error:
    raise SystemExit("Optional runner: install Modal in a separate environment before using modal run.") from error


PROJECT = Path(__file__).resolve().parents[2]
app = modal.App("collaborative-collusion-oversight")
outputs = modal.Volume.from_name("collusion-detection", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "pydantic>=2.6,<3",
        "numpy>=1.26",
        "pandas>=2",
        "openai>=1.60",
        "python-dotenv>=1",
        "jinja2>=3",
        "backoff>=2",
        "tqdm>=4",
        "matplotlib>=3.8",
        "pytest>=8",
    )
    .add_local_dir(PROJECT / "src", "/workspace/src", ignore=["**/__pycache__/**", "**/*.pyc"])
    .add_local_dir(
        PROJECT / "tests" / "detection", "/workspace/tests/detection", ignore=["**/__pycache__/**", "**/*.pyc"]
    )
    .add_local_dir(
        PROJECT.parent / "util" / "poseidon_compute",
        "/workspace/poseidon_compute",
        ignore=["**/__pycache__/**", "**/*.pyc"],
    )
    .add_local_dir(PROJECT / "configs" / "detection", "/workspace/configs/detection")
    .add_local_dir(PROJECT / "data", "/workspace/data")
    .add_local_file(PROJECT / "pyproject.toml", "/workspace/pyproject.toml")
    .add_local_file(PROJECT / "uv.lock", "/workspace/uv.lock")
    .add_local_file(PROJECT / "docs/detection/analyze_pilot.py", "/workspace/docs/detection/analyze_pilot.py")
)


@app.function(
    image=image,
    cpu=2,
    memory=4096,
    timeout=3600,
    max_containers=1,
    volumes={"/outputs": outputs},
    secrets=[modal.Secret.from_name("collusion-detection-provider")],
)
def remote_run(
    config_json: str,
    episodes_jsonl: str,
    live: bool = False,
    plan_json: str | None = None,
    acceptance_json: str | None = None,
) -> str:
    import json
    import os
    import subprocess
    import sys
    import uuid

    # Validate JSON before even invoking the ordinary CLI; credentials remain
    # in Modal Secrets/environment and are never serialized into function args.
    json.loads(config_json)
    destination = Path("/outputs") / uuid.uuid4().hex
    destination.mkdir(parents=True)
    config_path, episodes_path = destination / "config.json", destination / "episodes.jsonl"
    config_path.write_text(config_json)
    episodes_path.write_text(episodes_jsonl)
    os.environ["MPLCONFIGDIR"] = str(destination / ".matplotlib")
    # Acceptance tests choose ROOT/detection_outputs for their Matplotlib cache.
    # Redirect that runner-owned path to the writable persistent Volume as well.
    workspace = Path("/workspace")
    runtime = destination / "acceptance_workspace"
    runtime.mkdir()
    workspace_outputs = workspace / "detection_outputs"
    if workspace_outputs.is_symlink():
        workspace_outputs.unlink()
    elif workspace_outputs.exists():
        raise RuntimeError("Unexpected workspace detection_outputs directory; refusing to replace it.")
    workspace_outputs.symlink_to(runtime, target_is_directory=True)
    prefix = [sys.executable, "-m", "src.collusion_detection"]
    receipt_path = destination / "acceptance.json"
    generated_plan_path = destination / "remote-plan.json"
    supplied_plan_path = destination / "reviewed-plan.json"
    try:
        if live:
            if not plan_json or not acceptance_json:
                raise ValueError("Live remote execution requires a reviewed plan and local acceptance receipt.")
            sys.path.insert(0, str(workspace))
            from src.collusion_detection.cli import load_config
            from src.collusion_detection.safety import fingerprint

            receipt = json.loads(acceptance_json)
            if receipt.get("status") != "passed" or receipt.get("fingerprint") != fingerprint(load_config(config_path)):
                raise ValueError("Uploaded source/config differs from the passing local acceptance receipt.")
            supplied_plan_path.write_text(plan_json)
        subprocess.run(
            prefix + ["doctor", "--config", str(config_path), "--acceptance", str(receipt_path)],
            check=True,
            cwd=workspace,
        )
        subprocess.run(
            prefix
            + [
                "plan",
                "--dry-run",
                "--config",
                str(config_path),
                "--episodes",
                str(episodes_path),
                "--out",
                str(generated_plan_path),
            ],
            check=True,
            cwd=workspace,
        )
        if live and json.loads(generated_plan_path.read_text()) != json.loads(plan_json):
            raise ValueError("Remote zero-request expansion differs from the reviewed local plan.")
        command = prefix + [
            "run",
            "--config",
            str(config_path),
            "--episodes",
            str(episodes_path),
            "--out",
            str(destination / "run"),
        ]
        if live:
            command += ["--live", "--acceptance", str(receipt_path), "--plan", str(supplied_plan_path)]
        subprocess.run(command, check=True, cwd=workspace)
    finally:
        outputs.commit()
    return str(destination)


@app.local_entrypoint()
def main(config_path: str, episodes_path: str, live: bool = False, plan_path: str = "", acceptance_path: str = ""):
    config_json, episodes_jsonl = Path(config_path).read_text(), Path(episodes_path).read_text()
    plan_json, acceptance_json = None, None
    if live:
        if not plan_path or not acceptance_path:
            raise ValueError("--live requires --plan-path and --acceptance-path before remote dispatch.")
        import json
        import sys

        sys.path.insert(0, str(PROJECT))
        from src.collusion_detection.cli import load_config, load_episodes
        from src.collusion_detection.planning import check_live_plan, plan
        from src.collusion_detection.safety import require_acceptance

        config = load_config(config_path)
        require_acceptance(acceptance_path, config)
        plan_json, acceptance_json = Path(plan_path).read_text(), Path(acceptance_path).read_text()
        expansion = plan(config, load_episodes(episodes_path, config))
        check_live_plan(expansion, config)
        if json.loads(plan_json) != expansion:
            raise ValueError("--plan-path must contain the unchanged reviewed expansion for these inputs.")
    result = remote_run.remote(config_json, episodes_jsonl, live, plan_json, acceptance_json)
    print(f"Persistent output: {result} on Modal Volume collusion-detection")
    print("Retrieve with: modal volume get collusion-detection <run-directory> ./downloaded-run")
