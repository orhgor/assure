"""scripts/gen-env.sh writes one file and runs nothing else.

Why this test exists: the .env template is an unquoted bash heredoc (the
values must expand), and a comment in it read ``run `docker compose up -d`
again`` — inside a heredoc those backticks are a command substitution, so
generating the env file started the whole stack and pulled the Ollama image
(reported by the user on EC2 as "gen-env downloads ollama", reproduced with
``bash -x`` on 2026-09-25). The script must stay a pure generator.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen-env.sh"


def _stub_bin(tmp_path: Path) -> Path:
    """A PATH prefix where every tool the generator must NOT call leaves a mark."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("docker", "docker-compose", "curl", "wget", "ollama", "pip", "pip3", "npm", "apt-get", "python3"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{tmp_path}/called.log"\nexit 0\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run(tmp_path: Path, *args: str, stdin: str | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    out = tmp_path / "generated.env"
    env = dict(os.environ, PATH=f"{_stub_bin(tmp_path)}:{os.environ['PATH']}", FORCE="1")
    proc = subprocess.run(
        ["bash", str(SCRIPT), *args, str(out)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=30,
    )
    return proc, out


def test_heredoc_has_no_backticks() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index('cat > "$OUT" <<ENV')
    end = text.index("\nENV\n", start)
    assert "`" not in text[start:end], "a backtick inside the unquoted heredoc runs a command"


@pytest.mark.parametrize("target", ["ec2", "local"])
def test_generator_runs_nothing_and_writes_the_file(tmp_path: Path, target: str) -> None:
    proc, out = _run(tmp_path, target, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "called.log").exists(), (tmp_path / "called.log").read_text()
    values = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if line and not line.startswith("#")
    )
    assert values["ASSURE_LLM_BACKEND"] == "ollama"
    assert values["SHELL_PORT"] == "80"
    assert values["SHELL_BIND"] == ("0.0.0.0" if target == "ec2" else "127.0.0.1")
    assert values["ENVIRONMENT"] == ("production" if target == "ec2" else "development")
    assert len(values["PEM_SECRET_KEY"]) == 64 and len(values["POSTGRES_PASSWORD"]) == 48
    # Fernet: 44 urlsafe-base64 characters
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}=", values["ENCRYPTION_KEY"]), values["ENCRYPTION_KEY"]
    assert values["ASSURE_S3_BUCKET"] == "" and values["AWS_ACCESS_KEY_ID"] == ""
    assert "SHELL_ACCESS_KEY" in values and values["SHELL_ACCESS_KEY"]
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert "next:       docker compose up -d" in proc.stdout


def test_generator_asks_when_interactive(tmp_path: Path) -> None:
    """Piped answers stand in for a terminal: --yes absent, but stdin is not a
    tty, so the script must fall back to defaults instead of hanging."""
    proc, out = _run(tmp_path, "ec2", stdin="")
    assert proc.returncode == 0, proc.stderr
    assert "ASSURE_OLLAMA_MODEL=qwen2.5:1.5b" in out.read_text()


def test_generator_takes_answers_including_the_aws_key_pair(tmp_path: Path) -> None:
    """The user asked (2026-09-25) to be prompted for the access key and secret
    instead of editing the file afterwards: both are questions, the secret is
    read hidden, and empty answers keep the defaults."""
    answers = "\n".join(["AKIAEXAMPLE", "s3cr3t/with+chars", "eu-west-1", "my-bucket", "8080", "", "local", "n", "", "qwen2.5:7b", "", "", "", "0"]) + "\n"
    proc, out = _run(tmp_path, "ec2", "--ask", stdin=answers)
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "called.log").exists()
    text = out.read_text()
    assert "AWS_ACCESS_KEY_ID=AKIAEXAMPLE\n" in text
    assert "AWS_SECRET_ACCESS_KEY=s3cr3t/with+chars\n" in text
    assert "AWS_DEFAULT_REGION=eu-west-1\n" in text
    assert "ASSURE_S3_BUCKET=my-bucket\n" in text
    assert "SHELL_PORT=8080\n" in text and "SHELL_BIND=0.0.0.0\n" in text
    assert "ASSURE_OLLAMA_MODEL_DRAFT=qwen2.5:7b\n" in text and "ASSURE_OLLAMA_MODEL=qwen2.5:7b\n" in text
    assert "ASSURE_OLLAMA_MODEL_PARSE=qwen2.5:1.5b\n" in text and "ASSURE_OLLAMA_MODEL_COMPARE=llama3.2:1b\n" in text
    assert "ASSURE_TEXTRACT_MONTHLY_USD_CAP=0\n" in text
    prompts = proc.stdout + proc.stderr  # read -p writes the prompt to stderr
    assert "AWS access key id" in prompts and "secret access key" in prompts


def test_generator_gpu_answer_selects_the_overlay_and_bigger_models(tmp_path: Path) -> None:
    """'y' to the GPU question: COMPOSE_FILE adds docker-compose.gpu.yml so a plain
    `docker compose up -d` uses the card, and the 7B/8B tags become defaults."""
    answers = "\n".join(["", "", "", "", "", "", "", "y", "", "", "", "", "", "", ""]) + "\n"
    proc, out = _run(tmp_path, "ec2", "--ask", stdin=answers)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text()
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml\n" in text
    # 24 GB tier: 7b parse/evidence, 14b draft/red-hat, gemma3:27b compare
    for line in ("ASSURE_OLLAMA_MODEL_PARSE=qwen2.5:7b", "ASSURE_OLLAMA_MODEL_DRAFT=qwen2.5:14b",
                 "ASSURE_OLLAMA_MODEL_REDHAT=qwen2.5:14b", "ASSURE_OLLAMA_MODEL_EVIDENCE=qwen2.5:7b",
                 "ASSURE_OLLAMA_MODEL_COMPARE=gemma3:27b", "ASSURE_OLLAMA_MODEL=qwen2.5:14b", "OLLAMA_CONTEXT_LENGTH=16384"):
        assert line + "\n" in text, line
    # 80 GB tier
    answers = "\n".join(["", "", "", "", "", "", "", "y", "80", "", "", "", "", "", ""]) + "\n"
    proc, out = _run(tmp_path, "ec2", "--ask", stdin=answers)
    assert "ASSURE_OLLAMA_MODEL_DRAFT=qwen2.5:72b\n" in out.read_text() and "ASSURE_OLLAMA_MODEL_COMPARE=llama3.3:70b\n" in out.read_text()
    proc, out = _run(tmp_path, "ec2", "--yes")
    assert "COMPOSE_FILE=" not in out.read_text()  # no nvidia-smi in the stub PATH → CPU


def test_generator_bedrock_answer_sets_backend_and_both_roles(tmp_path: Path) -> None:
    """'bedrock' to the models question: backend bedrock, Sonnet 5 for drafting,
    Opus 5 for analysis (user decision 2026-09-25), overridable per role."""
    answers = "\n".join(["", "", "", "", "", "", "bedrock", "", "anthropic.claude-opus-5-5", "n", "", "", "", "", "", ""]) + "\n"
    proc, out = _run(tmp_path, "ec2", "--ask", stdin=answers)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text()
    assert "ASSURE_LLM_BACKEND=bedrock\n" in text
    assert "ASSURE_BEDROCK_MODEL_DRAFT=anthropic.claude-sonnet-5\n" in text
    assert "ASSURE_BEDROCK_MODEL_ANALYSIS=anthropic.claude-opus-5-5\n" in text
    proc, out = _run(tmp_path, "ec2", "--yes")
    assert "ASSURE_LLM_BACKEND=ollama\n" in out.read_text()  # default stays local


def test_generator_openrouter_answer_writes_key_and_stage_models(tmp_path: Path) -> None:
    # aws key, secret, region, bucket, port, bind, models-where, OR key, 7 stage models, gpu, 5 ollama models, textract
    answers = "\n".join(["", "", "", "", "", "", "openrouter", "sk-or-abc123", "", "", "", "", "", "", "", "n", "", "", "", "", "", ""]) + "\n"
    proc, out = _run(tmp_path, "ec2", "--ask", stdin=answers)
    assert proc.returncode == 0, proc.stderr
    text = out.read_text()
    assert "ASSURE_LLM_BACKEND=openrouter\n" in text and "OPENROUTER_API_KEY=sk-or-abc123\n" in text
    for line in ("ASSURE_OPENROUTER_MODEL_PARSE=amazon/nova-lite-v1", "ASSURE_OPENROUTER_MODEL_DRAFT=meta-llama/llama-3.3-70b-instruct",
                 "ASSURE_OPENROUTER_MODEL_ANCHOR=cohere/command-r7b-12-2024", "ASSURE_OPENROUTER_MODEL_EVIDENCE=mistralai/mistral-small-24b-instruct-2501",
                 "ASSURE_OPENROUTER_MODEL_EDIT=mistralai/mistral-small-24b-instruct-2501"):
        assert line + "\n" in text, line
    assert not (tmp_path / "called.log").exists()


def test_generator_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    out = tmp_path / "x.env"
    out.write_text("keep\n")
    env = dict(os.environ, PATH=f"{_stub_bin(tmp_path)}:{os.environ['PATH']}")
    env.pop("FORCE", None)
    proc = subprocess.run(["bash", str(SCRIPT), "ec2", "--yes", str(out)], capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert proc.returncode == 1 and "exists" in proc.stderr
    assert out.read_text() == "keep\n"
