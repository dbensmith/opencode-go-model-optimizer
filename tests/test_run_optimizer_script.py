"""Tests for scripts/run-optimizer.sh key resolution precedence."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-optimizer.sh"


def _make_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _setup_stubs(
    tmp_path: Path,
    *,
    op_script: str = "exit 1\n",
) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv_log = tmp_path / "uv.log"
    op_log = tmp_path / "op.log"

    _make_executable(
        bin_dir / "uv",
        f"""#!/usr/bin/env bash
printf 'AA_API_KEY=%s\\nARGS=%s\\nOP_SERVICE_ACCOUNT_TOKEN=%s\\n' \\
  "${{AA_API_KEY:-}}" "$*" "${{OP_SERVICE_ACCOUNT_TOKEN:-}}" > {str(uv_log)!r}
""",
    )
    _make_executable(
        bin_dir / "op",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {str(op_log)!r}
{op_script}
""",
    )
    return bin_dir, uv_log, op_log


def _run_script(
    bin_dir: Path,
    extra_env: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    base_path = os.environ.get("PATH", "/usr/bin:/bin")
    env = {
        "PATH": f"{bin_dir}:{base_path}",
        "HOME": str(bin_dir.parent),
        "TMPDIR": str(bin_dir.parent),
    }
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(_SCRIPT), *(args or [])],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_aa_api_key_takes_precedence_over_1password(tmp_path: Path) -> None:
    bin_dir, uv_log, op_log = _setup_stubs(
        tmp_path,
        op_script="""#!/usr/bin/env bash
echo "op should not be invoked when AA_API_KEY is set" >&2
exit 99
""",
    )
    proc = _run_script(
        bin_dir,
        {
            "AA_API_KEY": "env-secret-key",
            "OP_SERVICE_ACCOUNT_TOKEN": "sa-token",
            "OP_CONNECT_HOST": "https://connect.example.com",
            "AA_SECRET_REF": "op://vault/item/field",
        },
        args=["--plan", "go"],
    )

    assert proc.returncode == 0, proc.stderr
    assert "using AA_API_KEY from environment" in proc.stderr
    assert not op_log.exists()
    recorded = uv_log.read_text(encoding="utf-8")
    assert "AA_API_KEY=env-secret-key" in recorded
    assert (
        "ARGS=run opencode-go-model-optimizer select --mode pareto --format table --explain --plan go"
        in recorded
    )


def test_empty_aa_api_key_falls_back_to_1password(tmp_path: Path) -> None:
    bin_dir, uv_log, op_log = _setup_stubs(
        tmp_path,
        op_script="""#!/usr/bin/env bash
if [[ "$1" == "read" && "$2" == "--no-newline" && "$3" == "op://vault/item/field" ]]; then
  printf 'op-secret-key'
  exit 0
fi
exit 1
""",
    )
    proc = _run_script(
        bin_dir,
        {
            "AA_API_KEY": "",
            "OP_SERVICE_ACCOUNT_TOKEN": "sa-token",
            "AA_SECRET_REF": "op://vault/item/field",
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert "reading AA_API_KEY from 1Password (service-account)" in proc.stderr
    assert "read --no-newline op://vault/item/field" in op_log.read_text(encoding="utf-8")
    assert "AA_API_KEY=op-secret-key" in uv_log.read_text(encoding="utf-8")


def test_1password_connect_takes_precedence_over_service_account(tmp_path: Path) -> None:
    bin_dir, uv_log, _ = _setup_stubs(
        tmp_path,
        op_script="""#!/usr/bin/env bash
if [[ "$1" == "read" ]]; then
  printf 'connect-secret'
  exit 0
fi
exit 1
""",
    )
    proc = _run_script(
        bin_dir,
        {
            "OP_CONNECT_HOST": "https://connect.example.com",
            "OP_CONNECT_TOKEN": "connect-token",
            "OP_SERVICE_ACCOUNT_TOKEN": "sa-token",
            "AA_SECRET_REF": "op://vault/item/field",
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert "using Connect" in proc.stderr
    recorded = uv_log.read_text(encoding="utf-8")
    assert "AA_API_KEY=connect-secret" in recorded
    assert "OP_SERVICE_ACCOUNT_TOKEN=\n" in recorded


def test_fails_when_neither_aa_api_key_nor_1password_auth_present(tmp_path: Path) -> None:
    bin_dir, _, _ = _setup_stubs(
        tmp_path,
        op_script="""#!/usr/bin/env bash
if [[ "$1" == "account" ]]; then
  printf '[]'
  exit 0
fi
exit 1
""",
    )
    proc = _run_script(bin_dir, {})

    assert proc.returncode == 1
    assert "AA_API_KEY is unset and no 1Password auth" in proc.stderr
