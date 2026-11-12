& {
    $ErrorActionPreference = "Stop"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
    }
    if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        throw "Required command not found: tar"
    }

    $repository = "https://github.com/GEYUANwuqi/QuitBot"
    $release = Invoke-WebRequest "$repository/releases/latest" -UseBasicParsing
    if ($release.BaseResponse.ResponseUri) {
        $releaseUrl = $release.BaseResponse.ResponseUri.AbsoluteUri
    } else {
        $releaseUrl = $release.BaseResponse.RequestMessage.RequestUri.AbsoluteUri
    }
    $match = [regex]::Match($releaseUrl, "^" + [regex]::Escape("$repository/releases/tag/v") + "([0-9][0-9A-Za-z.+-]*)$")
    if (-not $match.Success) {
        throw "Could not resolve a valid latest release tag."
    }
    $version = $match.Groups[1].Value
    $installDir = Join-Path (Get-Location).Path "QuitBot_$version"
    if (Test-Path -LiteralPath $installDir) {
        throw "Directory already exists: $installDir"
    }

    $archive = [System.IO.Path]::GetTempFileName()
    try {
        Write-Host "Downloading QuitBot $version..."
        Invoke-WebRequest "$repository/releases/download/v$version/quitbot-$version.tar.gz" -OutFile $archive -UseBasicParsing
        New-Item -ItemType Directory -Path $installDir | Out-Null
        tar -xzf $archive -C $installDir --strip-components=1
        if ($LASTEXITCODE -ne 0) {
            throw "Source archive extraction failed: $installDir"
        }
        foreach ($required in @("pyproject.toml", "uv.lock", ".python-version", "src/quitbot_backend/_webui/index.html", "src/quitbot_backend/_webui/THIRD_PARTY_LICENSES.txt")) {
            if (-not (Test-Path -LiteralPath (Join-Path $installDir $required) -PathType Leaf)) {
                throw "Release archive is missing: $required"
            }
        }
        Push-Location -LiteralPath $installDir
        try {
            uv sync --locked --no-dev
            if ($LASTEXITCODE -ne 0) {
                throw "Dependency installation failed. Files are kept in $installDir; fix the reported error and run uv sync --locked --no-dev there."
            }
        } finally {
            Pop-Location
        }
    } finally {
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "Installed QuitBot $version in $installDir"
    Write-Host "Start from the project directory:"
    Write-Host ("  Set-Location -LiteralPath '" + $installDir.Replace("'", "''") + "'")
    Write-Host "  uv run --locked --no-dev python -m quitbot_backend"
}
