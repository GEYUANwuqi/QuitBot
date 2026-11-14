& {
    $ErrorActionPreference = "Stop"

    function Get-QuitBotVersion([string]$Directory) {
        $metadata = Join-Path $Directory "pyproject.toml"
        if (-not (Test-Path -LiteralPath $metadata -PathType Leaf)) { return }
        $section = ""
        $packageName = ""
        $packageVersion = ""
        foreach ($line in Get-Content -LiteralPath $metadata) {
            $sectionMatch = [regex]::Match($line, '^\s*\[([^\]]+)\]')
            if ($sectionMatch.Success) {
                $section = $sectionMatch.Groups[1].Value
            } elseif ($section -eq "project") {
                $field = [regex]::Match($line, '^\s*(name|version)\s*=\s*[''"]([^''"]+)[''"]\s*(#.*)?$')
                if ($field.Success) {
                    if ($field.Groups[1].Value -eq "name") {
                        $packageName = $field.Groups[2].Value
                    } else {
                        $packageVersion = $field.Groups[2].Value
                    }
                }
            }
        }
        if ($packageName -eq "quitbot" -and $packageVersion -match '^[0-9][0-9A-Za-z.+-]*$') {
            return $packageVersion
        }
    }

    function Read-Choice([string]$Prompt) {
        try { return (Read-Host $Prompt).Trim() }
        catch { throw "An interactive confirmation is required; nothing was installed. $($_.Exception.Message)" }
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
    }
    if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        throw "Required command not found: tar"
    }

    $currentDir = (Get-Location).Path
    $baseDir = $currentDir
    $sourceDir = $null
    $sourceVersion = Get-QuitBotVersion $currentDir
    if ($sourceVersion) {
        $sourceDir = $currentDir
        $parent = [System.IO.Directory]::GetParent($currentDir)
        if (-not $parent) { throw "The project directory needs a parent directory for the upgrade." }
        $baseDir = $parent.FullName
    } else {
        $candidates = @(
            Get-ChildItem -LiteralPath $currentDir -Directory -Filter "QuitBot_*" |
                Sort-Object Name |
                ForEach-Object {
                    if (-not ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                        $candidateVersion = Get-QuitBotVersion $_.FullName
                        if ($candidateVersion) {
                            [pscustomobject]@{ Path = $_.FullName; Version = $candidateVersion }
                        }
                    }
                }
        )
        if ($candidates.Count -eq 1) {
            $sourceDir = $candidates[0].Path
            $sourceVersion = $candidates[0].Version
        } elseif ($candidates.Count -gt 1) {
            Write-Host "Choose the installation whose configuration should be kept:"
            for ($index = 0; $index -lt $candidates.Count; $index++) {
                Write-Host ("  {0}) {1} (version {2})" -f ($index + 1), $candidates[$index].Path, $candidates[$index].Version)
            }
            while ($true) {
                $answer = Read-Choice "Installation number (0 to cancel)"
                if ($answer -eq "" -or $answer -eq "0") { Write-Host "Cancelled."; return }
                $selected = 0
                if ([int]::TryParse($answer, [ref]$selected) -and $selected -ge 1 -and $selected -le $candidates.Count) {
                    $sourceDir = $candidates[$selected - 1].Path
                    $sourceVersion = $candidates[$selected - 1].Version
                    break
                }
                Write-Host "Enter one of the listed numbers."
            }
        }
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
    if ($sourceDir -and $sourceVersion -eq $version) {
        Write-Host "QuitBot $version is already the latest release: $sourceDir"
        return
    }
    $installDir = Join-Path $baseDir "QuitBot_$version"
    if (Test-Path -LiteralPath $installDir) {
        throw "Directory already exists: $installDir"
    }
    if ($sourceDir) {
        $sourceConfig = Join-Path $sourceDir "config"
        if ((Test-Path -LiteralPath $sourceConfig) -and -not (Test-Path -LiteralPath $sourceConfig -PathType Container)) {
            throw "Configuration is not a readable directory: $sourceConfig"
        }
        Write-Host "Upgrade QuitBot $sourceVersion -> $version"
        Write-Host "  From: $sourceDir"
        Write-Host "  To:   $installDir"
        Write-Host "The complete config/ directory will be copied, including login settings and message state."
        Write-Host "The old installation will be kept. Stop its running backend before continuing."
        while ($true) {
            $answer = Read-Choice "Continue with the upgrade? [y/N]"
            if ($answer -eq "y") { break }
            if ($answer -eq "n" -or $answer -eq "") { Write-Host "Cancelled."; return }
            Write-Host "Enter y or n."
        }
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
        if ((Get-QuitBotVersion $installDir) -ne $version) {
            throw "Release archive has an unexpected project name or version."
        }
        if (Test-Path -LiteralPath (Join-Path $installDir "config")) {
            throw "Release archive unexpectedly contains config/. Installation stopped."
        }
        if ($sourceDir -and (Test-Path -LiteralPath $sourceConfig -PathType Container)) {
            Copy-Item -LiteralPath $sourceConfig -Destination $installDir -Recurse -Force
            Write-Host "Configuration preserved from $sourceConfig"
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
