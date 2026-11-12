#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf 'QuitBot: %s\n' "$*" >&2
    exit 1
}

command -v uv >/dev/null 2>&1 || fail 'uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/'
for command in curl tar; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

repository='https://github.com/GEYUANwuqi/QuitBot'
release_url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$repository/releases/latest")
prefix="$repository/releases/tag/v"
[[ "$release_url" == "$prefix"* ]] || fail 'Could not resolve the latest release tag.'
version=${release_url#"$prefix"}
[[ "$version" =~ ^[0-9][0-9A-Za-z.+-]*$ ]] || fail 'Invalid release version.'

install_dir="$PWD/QuitBot_$version"
[[ ! -e "$install_dir" && ! -L "$install_dir" ]] || fail "Directory already exists: $install_dir"
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT
archive="$temporary/quitbot.tar.gz"

printf 'Downloading QuitBot %s...\n' "$version"
curl -fL --retry 2 -o "$archive" "$repository/releases/download/v$version/quitbot-$version.tar.gz"
mkdir -- "$install_dir"
tar -xzf "$archive" -C "$install_dir" --strip-components=1
for required in pyproject.toml uv.lock .python-version src/quitbot_backend/_webui/index.html src/quitbot_backend/_webui/THIRD_PARTY_LICENSES.txt; do
    [[ -f "$install_dir/$required" ]] || fail "Release archive is missing: $required"
done

if ! (cd "$install_dir" && uv sync --locked --no-dev); then
    fail "Dependency installation failed. Files are kept in $install_dir; fix the reported error and run uv sync --locked --no-dev there."
fi

printf '\nInstalled QuitBot %s in %s\n' "$version" "$install_dir"
printf 'Start from the project directory:\n  cd %q\n  uv run --locked --no-dev python -m quitbot_backend\n' "$install_dir"
printf 'For a headless Linux desktop:\n  uv run --locked --no-dev python -m quitbot_backend.desktop --display :99 --qq /opt/QQ/qq\n'
