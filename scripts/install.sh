#!/usr/bin/env bash
set -euo pipefail

fail() {
    printf 'QuitBot: %s\n' "$*" >&2
    exit 1
}

project_version() {
    local line section='' name='' found_version=''
    local assignment="^[[:space:]]*(name|version)[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"][[:space:]]*(#.*)?$"
    [[ -f "$1/pyproject.toml" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^[[:space:]]*\[([^]]+)\] ]]; then
            section=${BASH_REMATCH[1]}
        elif [[ "$section" == project && "$line" =~ $assignment ]]; then
            case ${BASH_REMATCH[1]} in
                name) name=${BASH_REMATCH[2]} ;;
                version) found_version=${BASH_REMATCH[2]} ;;
            esac
        fi
    done < "$1/pyproject.toml"
    [[ "$name" == quitbot && "$found_version" =~ ^[0-9][0-9A-Za-z.+-]*$ ]] || return 1
    printf '%s\n' "$found_version"
}

read_choice() {
    local terminal_fd
    printf '%s' "$1" >&2
    # curl | bash uses stdin for the script itself, so read its answer from tty.
    if { exec {terminal_fd}</dev/tty; } 2>/dev/null; then
        if ! IFS= read -r -u "$terminal_fd" installer_answer; then
            exec {terminal_fd}<&-
            fail 'No confirmation received; nothing was installed.'
        fi
        exec {terminal_fd}<&-
    elif [[ -f ${BASH_SOURCE[0]:-} ]]; then
        IFS= read -r installer_answer || fail 'No confirmation received; nothing was installed.'
    else
        fail 'An interactive terminal is required to confirm an upgrade. Download the script and run it with bash.'
    fi
    installer_answer=${installer_answer#"${installer_answer%%[![:space:]]*}"}
    installer_answer=${installer_answer%"${installer_answer##*[![:space:]]}"}
}

command -v uv >/dev/null 2>&1 || fail 'uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/'
for command in curl tar cp; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

current_dir=$(pwd -P)
base_dir=$current_dir
source_dir=''
source_version=''
if source_version=$(project_version "$current_dir"); then
    source_dir=$current_dir
    base_dir=$(cd "$current_dir/.." && pwd -P)
else
    candidates=()
    candidate_versions=()
    for candidate in "$current_dir"/QuitBot_*; do
        [[ -d "$candidate" && ! -L "$candidate" ]] || continue
        if candidate_version=$(project_version "$candidate"); then
            candidates+=("$candidate")
            candidate_versions+=("$candidate_version")
        fi
    done
    if (( ${#candidates[@]} == 1 )); then
        source_dir=${candidates[0]}
        source_version=${candidate_versions[0]}
    elif (( ${#candidates[@]} > 1 )); then
        printf 'Choose the installation whose configuration should be kept:\n'
        for index in "${!candidates[@]}"; do
            printf '  %d) %s (version %s)\n' "$((index + 1))" "${candidates[index]}" "${candidate_versions[index]}"
        done
        while true; do
            read_choice 'Installation number (0 to cancel): '
            case "$installer_answer" in
                ''|0) printf 'Cancelled.\n'; exit 0 ;;
            esac
            if [[ "$installer_answer" =~ ^[1-9][0-9]*$ && ${#installer_answer} -le 9 ]]; then
                index=$((10#$installer_answer - 1))
                if (( index < ${#candidates[@]} )); then
                    source_dir=${candidates[index]}
                    source_version=${candidate_versions[index]}
                    break
                fi
            fi
            printf 'Enter one of the listed numbers.\n' >&2
        done
    fi
fi

repository='https://github.com/GEYUANwuqi/QuitBot'
release_url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$repository/releases/latest")
prefix="$repository/releases/tag/v"
[[ "$release_url" == "$prefix"* ]] || fail 'Could not resolve the latest release tag.'
version=${release_url#"$prefix"}
[[ "$version" =~ ^[0-9][0-9A-Za-z.+-]*$ ]] || fail 'Invalid release version.'

if [[ -n "$source_dir" && "$source_version" == "$version" ]]; then
    printf 'QuitBot %s is already the latest release: %s\n' "$version" "$source_dir"
    exit 0
fi

install_dir="$base_dir/QuitBot_$version"
[[ ! -e "$install_dir" && ! -L "$install_dir" ]] || fail "Directory already exists: $install_dir"
if [[ -n "$source_dir" ]]; then
    if [[ -e "$source_dir/config" || -L "$source_dir/config" ]]; then
        [[ -d "$source_dir/config" ]] || fail "Configuration is not a readable directory: $source_dir/config"
    fi
    printf 'Upgrade QuitBot %s -> %s\n  From: %s\n  To:   %s\n' "$source_version" "$version" "$source_dir" "$install_dir"
    printf 'The complete config/ directory will be copied, including login settings and message state.\n'
    printf 'The old installation will be kept. Stop its running backend before continuing.\n'
    while true; do
        read_choice 'Continue with the upgrade? [y/N]: '
        case "$installer_answer" in
            y|Y) break ;;
            n|N|'') printf 'Cancelled.\n'; exit 0 ;;
            *) printf 'Enter y or n.\n' >&2 ;;
        esac
    done
fi

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
[[ $(project_version "$install_dir") == "$version" ]] || fail 'Release archive has an unexpected project name or version.'
[[ ! -e "$install_dir/config" && ! -L "$install_dir/config" ]] || fail 'Release archive unexpectedly contains config/. Installation stopped.'

if [[ -n "$source_dir" && -d "$source_dir/config" ]]; then
    cp -a -- "$source_dir/config" "$install_dir/config" || fail "Configuration copy failed. The source is kept in $source_dir; the new directory is incomplete."
    printf 'Configuration preserved from %s/config\n' "$source_dir"
fi

if ! (cd "$install_dir" && uv sync --locked --no-dev); then
    fail "Dependency installation failed. Files are kept in $install_dir; fix the reported error and run uv sync --locked --no-dev there."
fi

printf '\nInstalled QuitBot %s in %s\n' "$version" "$install_dir"
printf 'Start from the project directory:\n  cd %q\n  uv run --locked --no-dev python -m quitbot_backend\n' "$install_dir"
printf 'For a headless Linux desktop:\n  uv run --locked --no-dev python -m quitbot_backend.desktop --display :99 --qq /opt/QQ/qq\n'
