"""Install/upgrade boundary checks with synthetic releases and preserved user state."""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "9.8.7"


def metadata(version):
    return (
        '[project]\nname = "quitbot"\n'
        f'version = "{version}"\n'
        '[tool.fixture]\nversion = "1.0.0"\n'
    ).encode()


def release_archive(path, *, assets=True, version=VERSION, bundled_config=False):
    files = {
        "pyproject.toml": metadata(version),
        "uv.lock": b"synthetic lock fixture\n",
        ".python-version": b"3.12\n",
        "src/quitbot_backend/_webui/THIRD_PARTY_LICENSES.txt": b"fixture license\n",
    }
    if assets:
        files["src/quitbot_backend/_webui/index.html"] = b"fixture console\n"
    if bundled_config:
        files["config/accounts.json"] = b"unexpected bundled settings"
    with tarfile.open(path, "w:gz") as output:
        for name, data in files.items():
            info = tarfile.TarInfo(f"quitbot-{VERSION}/{name}")
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))


def old_installation(path, version="9.8.6", *, config=True):
    path.mkdir()
    (path / "pyproject.toml").write_bytes(metadata(version))
    (path / "local-code.txt").write_text("local source changes")
    (path / ".venv").mkdir()
    (path / ".venv/keep.txt").write_text("old environment")
    if config:
        values = {
            "accounts.json": json.dumps({"fixture_version": version}).encode(),
            "security.json": b'{"fixture": "existing password digest"}',
            "state/10001/messages.sqlite3": b"SQLite format 3\x00fixture",
            "state/10001/images/fixture.png": b"fixture image",
            ".hidden-setting": b"preserve hidden configuration",
        }
        for name, data in values.items():
            target = path / "config" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return path


def files_below(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture(params=["bash", "powershell"])
def installer(tmp_path, request):
    shell = request.param
    executable = shutil.which("bash" if shell == "bash" else "pwsh")
    if not executable or (shell == "bash" and sys.platform == "win32"):
        pytest.skip(f"{shell} is not available for this platform")
    work = tmp_path / "directory with spaces [fixture]"
    work.mkdir()
    archive = tmp_path / "release.tar.gz"
    release_archive(archive)
    calls = tmp_path / "uv-calls.txt"
    network = tmp_path / "network.txt"
    prompts = tmp_path / "prompts.txt"
    env = {
        **os.environ,
        "INSTALL_TEST_ARCHIVE": str(archive),
        "INSTALL_TEST_CALLS": str(calls),
        "INSTALL_TEST_NETWORK": str(network),
        "INSTALL_TEST_PROMPTS": str(prompts),
        "INSTALL_TEST_LATEST": f"https://github.com/GEYUANwuqi/QuitBot/releases/tag/v{VERSION}",
        "INSTALL_TEST_SCRIPT": str(ROOT / "scripts" / ("install.sh" if shell == "bash" else "install.ps1")),
    }
    if shell == "bash":
        binaries = tmp_path / "bin"
        binaries.mkdir()
        for name in ("tar", "gzip", "mktemp", "rm", "mkdir", "cp", "cat"):
            (binaries / name).symlink_to(shutil.which(name))
        commands = {
            "curl": """
printf '%s\\n' "$*" >> "$INSTALL_TEST_NETWORK"
if [[ "${!#}" == */releases/latest ]]; then
    printf '%s' "$INSTALL_TEST_LATEST"
else
    while [[ "$1" != '-o' ]]; do shift; done
    cp "$INSTALL_TEST_ARCHIVE" "$2"
fi
""",
            "uv": """
printf '%s\\n' "$PWD" "$@" > "$INSTALL_TEST_CALLS"
exit "$INSTALL_TEST_SYNC_EXIT"
""",
        }
        for name, body in commands.items():
            path = binaries / name
            path.write_text(f"#!{executable}\nset -eu\n{body}")
            path.chmod(0o755)
        env["PATH"] = str(binaries)
        env["INSTALL_TEST_BASH"] = executable
        command = [executable, env["INSTALL_TEST_SCRIPT"]]
    else:
        harness = tmp_path / "harness.ps1"
        harness.write_text("""
function Get-Command {
    [CmdletBinding()]
    param([string]$Name)
    if ($Name -eq 'uv' -and $env:INSTALL_TEST_MISSING_UV -eq '1') { return }
    Microsoft.PowerShell.Core\\Get-Command $Name -ErrorAction SilentlyContinue
}
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    Add-Content -LiteralPath $env:INSTALL_TEST_NETWORK -Value $Uri
    if ($Uri.EndsWith('/releases/latest')) {
        return [pscustomobject]@{ BaseResponse = [pscustomobject]@{
            RequestMessage = [pscustomobject]@{ RequestUri = [uri]$env:INSTALL_TEST_LATEST }
        } }
    }
    Copy-Item -LiteralPath $env:INSTALL_TEST_ARCHIVE -Destination $OutFile
}
function uv {
    Set-Content -LiteralPath $env:INSTALL_TEST_CALLS -Encoding UTF8 -Value (@((Get-Location).Path) + $args)
    $global:LASTEXITCODE = [int]$env:INSTALL_TEST_SYNC_EXIT
}
$script:answers = @($env:INSTALL_TEST_ANSWERS | ConvertFrom-Json)
$script:answerIndex = 0
function Read-Host {
    param([string]$Prompt)
    Add-Content -LiteralPath $env:INSTALL_TEST_PROMPTS -Value $Prompt
    if ($script:answerIndex -ge $script:answers.Count) { throw 'No confirmation input' }
    $answer = $script:answers[$script:answerIndex]
    $script:answerIndex++
    return $answer
}
Invoke-Expression (Get-Content -LiteralPath $env:INSTALL_TEST_SCRIPT -Raw)
""", encoding="utf-8")
        command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(harness)]

    def run(cwd=None, *, answers=(), missing_uv=False, sync_failure=False, piped=False):
        if missing_uv and shell == "bash":
            (binaries / "uv").unlink()
        run_env = {
            **env,
            "INSTALL_TEST_MISSING_UV": str(int(missing_uv)),
            "INSTALL_TEST_SYNC_EXIT": "23" if sync_failure else "0",
            "INSTALL_TEST_ANSWERS": json.dumps(answers),
        }
        if piped:
            assert shell == "bash"
            arguments = [executable]
            user_input = Path(env["INSTALL_TEST_SCRIPT"]).read_text()
        else:
            arguments = command
            user_input = "".join(answer + "\n" for answer in answers)
        return subprocess.run(
            arguments, cwd=cwd or work, env=run_env, input=user_input,
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
            start_new_session=sys.platform != "win32",
        )

    return SimpleNamespace(
        run=run, work=work, target=work / f"QuitBot_{VERSION}", archive=archive,
        calls=calls, network=network, prompts=prompts, shell=shell, env=env,
        executable=executable,
    )


def assert_synced(installer):
    recorded = installer.calls.read_text(encoding="utf-8-sig").splitlines()
    assert Path(recorded[0]) == installer.target
    assert recorded[1:] == ["sync", "--locked", "--no-dev"]
    assert (installer.target / "src/quitbot_backend/_webui/index.html").is_file()


@pytest.mark.parametrize("case", ["success", "missing_uv", "existing", "sync_failure", "missing_assets"])
def test_installer_boundaries(installer, case):
    if case == "existing":
        installer.target.mkdir()
        (installer.target / "keep.txt").write_text("existing user data")
    if case == "missing_assets":
        release_archive(installer.archive, assets=False)
    result = installer.run(missing_uv=case == "missing_uv", sync_failure=case == "sync_failure")
    output = result.stdout + result.stderr
    assert (result.returncode == 0) == (case == "success"), output
    if case in ("success", "sync_failure"):
        assert_synced(installer)
        assert ("Installed QuitBot" in output) == (case == "success")
    else:
        assert not installer.calls.exists()
    if case == "missing_uv":
        assert "uv is required" in output
        assert not installer.network.exists() and not installer.target.exists()
    elif case == "existing":
        assert "Directory already exists" in output
        assert files_below(installer.target) == {"keep.txt": b"existing user data"}
    elif case == "missing_assets":
        assert "Release archive is missing" in output


@pytest.mark.parametrize("location", ["root", "parent", "multiple"])
def test_upgrade_preserves_complete_config_and_source(installer, location):
    source = old_installation(installer.work / ("checkout" if location == "root" else "QuitBot_9.8.6"))
    answers = ["Y"]
    if location == "multiple":
        source = old_installation(installer.work / "QuitBot_9.8.5", "9.8.5")
        answers = ["999", "not a number", "1", "y"]
    # A similarly named unrelated directory must not become an upgrade source.
    (installer.work / "QuitBot_notes").mkdir()
    before = files_below(source)
    result = installer.run(source if location == "root" else None, answers=answers)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(source) in result.stdout and str(installer.target) in result.stdout
    assert files_below(source) == before
    assert files_below(installer.target / "config") == files_below(source / "config")
    assert not (installer.target / "local-code.txt").exists()
    assert not (installer.target / ".venv/keep.txt").exists()
    assert_synced(installer)


@pytest.mark.parametrize("answers", [["n"], [""], ["yes", "n"], []])
def test_upgrade_requires_explicit_confirmation_before_writes(installer, answers):
    source = old_installation(installer.work / "QuitBot_9.8.6")
    before = files_below(source)
    result = installer.run(source, answers=answers)
    assert (result.returncode == 0) == bool(answers), result.stdout + result.stderr
    assert not installer.target.exists() and not installer.calls.exists()
    assert files_below(source) == before
    assert len(installer.network.read_text(encoding="utf-8-sig").splitlines()) == 1


def test_multiple_installs_can_be_cancelled_before_download(installer):
    old_installation(installer.work / "QuitBot_9.8.5", "9.8.5")
    old_installation(installer.work / "QuitBot_9.8.6")
    before = files_below(installer.work)
    result = installer.run(answers=["0"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert files_below(installer.work) == before
    assert not installer.network.exists() and not installer.calls.exists()


@pytest.mark.parametrize("location", ["root", "parent"])
def test_latest_installation_is_unchanged(installer, location):
    source = old_installation(installer.target, VERSION)
    before = files_below(installer.work)
    result = installer.run(source if location == "root" else None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already the latest" in result.stdout
    assert not installer.calls.exists() and not installer.prompts.exists()
    assert files_below(installer.work) == before
    assert len(installer.network.read_text(encoding="utf-8-sig").splitlines()) == 1


def test_upgrade_keeps_config_when_dependency_installation_fails(installer):
    source = old_installation(installer.work / "QuitBot_9.8.6")
    before = files_below(source)
    result = installer.run(source, answers=["y"], sync_failure=True)
    assert result.returncode != 0
    assert "Dependency installation failed" in result.stdout + result.stderr
    assert files_below(source) == before
    assert files_below(installer.target / "config") == files_below(source / "config")
    assert_synced(installer)


@pytest.mark.parametrize("problem", ["destination", "config_file", "archive_version", "archive_config"])
def test_upgrade_rejects_conflicts_and_incorrect_releases(installer, problem):
    source = old_installation(installer.work / "QuitBot_9.8.6", config=problem != "config_file")
    if problem == "destination":
        installer.target.mkdir()
        (installer.target / "keep.txt").write_bytes(b"keep destination")
    elif problem == "config_file":
        (source / "config").write_bytes(b"unexpected configuration file")
    elif problem == "archive_version":
        release_archive(installer.archive, version="9.7.0")
    else:
        release_archive(installer.archive, bundled_config=True)
    before = files_below(source)
    result = installer.run(source, answers=["y"])
    assert result.returncode != 0, result.stdout + result.stderr
    assert files_below(source) == before and not installer.calls.exists()
    if problem == "destination":
        assert files_below(installer.target) == {"keep.txt": b"keep destination"}


def test_upgrade_without_local_config(installer):
    source = old_installation(installer.work / "checkout", config=False)
    result = installer.run(source, answers=["y"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (installer.target / "config").exists()
    assert_synced(installer)


def test_piped_bash_refuses_upgrade_without_a_terminal(installer):
    if installer.shell != "bash":
        pytest.skip("Bash pipeline-specific input handling")
    source = old_installation(installer.work / "QuitBot_9.8.6")
    result = installer.run(source, piped=True)
    assert result.returncode != 0
    assert "interactive terminal" in result.stdout + result.stderr
    assert not installer.calls.exists() and not installer.target.exists()


@pytest.mark.parametrize("answer", ["y", "n"])
def test_piped_bash_reads_upgrade_confirmation_from_controlling_terminal(installer, answer):
    if installer.shell != "bash":
        pytest.skip("Bash pipeline-specific input handling")
    import errno
    import pty
    import select
    import signal
    import time

    source = old_installation(installer.work / "QuitBot_9.8.6")
    before = files_below(source)
    pid, terminal = pty.fork()
    if pid == 0:
        os.chdir(source)
        os.execve(
            installer.executable,
            [installer.executable, "-c", 'cat "$INSTALL_TEST_SCRIPT" | "$INSTALL_TEST_BASH"'],
            {**installer.env, "INSTALL_TEST_SYNC_EXIT": "0"},
        )
    output, answered, finished = b"", False, False
    deadline = time.monotonic() + 20
    try:
        while time.monotonic() < deadline:
            if select.select([terminal], [], [], 0.1)[0]:
                try:
                    data = os.read(terminal, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not data:
                    break
                output += data
                if not answered and b"Continue with the upgrade? [y/N]:" in output:
                    os.write(terminal, (answer + "\n").encode())
                    answered = True
            reaped, status = os.waitpid(pid, os.WNOHANG)
            if reaped:
                finished = True
                break
        else:
            pytest.fail(f"Installer did not finish: {output.decode(errors='replace')}")
        if not finished:
            _, status = os.waitpid(pid, 0)
            finished = True
        assert os.waitstatus_to_exitcode(status) == 0, output.decode(errors="replace")
        assert answered
    finally:
        if not finished:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(terminal)
    assert files_below(source) == before
    if answer == "y":
        assert_synced(installer)
        assert files_below(installer.target / "config") == files_below(source / "config")
    else:
        assert not installer.calls.exists() and not installer.target.exists()
