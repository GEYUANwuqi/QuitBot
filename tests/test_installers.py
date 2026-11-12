"""Exercise installer failures without contacting GitHub or changing Python environments."""

import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "9.8.7"


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("case", ["success", "missing_uv", "existing", "sync_failure", "missing_assets"])
def test_installer_boundaries(tmp_path, shell, case):
    executable = shutil.which("bash" if shell == "bash" else "pwsh")
    if not executable or (shell == "bash" and sys.platform == "win32"):
        pytest.skip(f"{shell} is not available for this platform")

    work = tmp_path / "directory with spaces"
    work.mkdir()
    target = work / f"QuitBot_{VERSION}"
    if case == "existing":
        target.mkdir()
        (target / "keep.txt").write_text("existing user data")
    archive = tmp_path / "release.tar.gz"
    files = [
        "pyproject.toml", "uv.lock", ".python-version",
        "src/quitbot_backend/_webui/THIRD_PARTY_LICENSES.txt",
    ]
    if case != "missing_assets":
        files.append("src/quitbot_backend/_webui/index.html")
    with tarfile.open(archive, "w:gz") as output:
        for name in files:
            data = b"synthetic installer fixture\n"
            info = tarfile.TarInfo(f"quitbot-{VERSION}/{name}")
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))

    calls = tmp_path / "uv-calls.txt"
    network = tmp_path / "network.txt"
    env = {
        **os.environ,
        "INSTALL_TEST_ARCHIVE": str(archive),
        "INSTALL_TEST_CALLS": str(calls),
        "INSTALL_TEST_NETWORK": str(network),
        "INSTALL_TEST_LATEST": f"https://github.com/GEYUANwuqi/QuitBot/releases/tag/v{VERSION}",
        "INSTALL_TEST_MISSING_UV": "1" if case == "missing_uv" else "0",
        "INSTALL_TEST_SYNC_EXIT": "23" if case == "sync_failure" else "0",
        "INSTALL_TEST_SCRIPT": str(ROOT / "scripts/install.ps1"),
    }

    if shell == "bash":
        binaries = tmp_path / "bin"
        binaries.mkdir()
        for name in ("tar", "gzip", "mktemp", "rm", "mkdir", "cp"):
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
        }
        if case != "missing_uv":
            commands["uv"] = """
printf '%s\\n' "$PWD" "$@" > "$INSTALL_TEST_CALLS"
exit "$INSTALL_TEST_SYNC_EXIT"
"""
        for name, body in commands.items():
            path = binaries / name
            path.write_text(f"#!{executable}\nset -eu\n{body}")
            path.chmod(0o755)
        env["PATH"] = str(binaries)
        command = [executable, str(ROOT / "scripts/install.sh")]
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
& $env:INSTALL_TEST_SCRIPT
""", encoding="utf-8")
        command = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(harness)]

    result = subprocess.run(
        command, cwd=work, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    output = result.stdout + result.stderr
    assert (result.returncode == 0) == (case == "success"), output
    if case in ("success", "sync_failure"):
        recorded = calls.read_text(encoding="utf-8-sig").splitlines()
        assert Path(recorded[0]) == target
        assert recorded[1:] == ["sync", "--locked", "--no-dev"]
        assert (target / "src/quitbot_backend/_webui/index.html").is_file()
        assert ("Installed QuitBot" in output) == (case == "success")
    else:
        assert not calls.exists()
    if case == "missing_uv":
        assert "uv is required" in output
        assert not network.exists() and not target.exists()
    elif case == "existing":
        assert "Directory already exists" in output
        assert list(target.iterdir()) == [target / "keep.txt"]
        assert (target / "keep.txt").read_text() == "existing user data"
    elif case == "missing_assets":
        assert "Release archive is missing" in output
