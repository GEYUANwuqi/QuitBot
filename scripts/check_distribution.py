"""Inspect built archives and smoke-test the installed wheel outside the checkout."""

import argparse
import re
import subprocess
import sys
import tarfile
import zipfile
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

SMOKE_TEST = r'''
import re
from pathlib import Path

from fastapi.testclient import TestClient
from quitbot_backend import management
from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import ConfigStore
from quitbot_backend.service import Service

# An unrelated working directory must not supply the console's files.
unrelated = Path("frontend/dist")
unrelated.mkdir(parents=True)
(unrelated / "index.html").write_text("unrelated working-directory content")
access = AccessKeys(Path("config/security.json"))
service = Service(ConfigStore(Path("config/accounts.json")), access)
assets = Path(management.__file__).with_name("_webui")
with TestClient(management.create_management_app(service)) as client:
    page = client.get("/")
    assert page.status_code == 200
    assert page.content == (assets / "index.html").read_bytes()
    urls = re.findall(r'(?:src|href)="(/[^\"]+)"', page.text)
    assert any(url.endswith(".js") for url in urls)
    assert any(url.endswith(".css") for url in urls)
    for url in [*urls, "/THIRD_PARTY_LICENSES.txt"]:
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.content == (assets / url.lstrip("/")).read_bytes(), url
    assert client.get("/api/config").status_code == 401
    login = client.post("/auth/login", json={"password": access.initial_password})
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get("/api/config", headers=headers).status_code == 403
    changed = client.post("/auth/password", headers=headers,
                          json={"new_password": "distribution-fixture-password"})
    assert changed.status_code == 200
    headers = {"Authorization": "Bearer " + changed.json()["access_token"]}
    response = client.get("/api/config", headers=headers)
    assert response.status_code == 200 and response.json()["config"]["accounts"] == []
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/config", headers=headers).status_code == 401
print("Installed wheel: console, assets, authentication and logout passed")
'''


def read_archive(path):
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
    with tarfile.open(path) as archive:
        result = {}
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f"Unexpected archive entry: {member.name}")
            name = member.name.split("/", 1)[1]
            result[name] = archive.extractfile(member).read()
        return result


def check_contents(path):
    files = read_archive(path)
    private_directories = {
        "config", "dev", ".local", ".git", ".venv", "node_modules", "__pycache__",
        "spy++", "spypp", "flauinspect",
    }
    for name in files:
        parts = PurePosixPath(name).parts
        if (
            set(parts[:-1]) & private_directories
            or any(part.startswith(".env") for part in parts)
            or name.endswith((".pyc", ".db", ".sqlite", ".sqlite3", ".log", ".map"))
            or re.search(r"(?:snowluma|napcat)_catalog", name)
        ):
            raise ValueError(f"Local data in {path.name}: {name}")
    prefix = ("" if path.suffix == ".whl" else "src/") + "quitbot_backend/_webui/"
    assets = {n.removeprefix(prefix): data for n, data in files.items() if n.startswith(prefix)}
    assert assets.get("index.html"), f"Missing console in {path.name}"
    assert assets.get("THIRD_PARTY_LICENSES.txt"), f"Missing notices in {path.name}"
    urls = re.findall(r'(?:src|href)="(/[^\"]+)"', assets["index.html"].decode())
    assert any(url.endswith(".js") for url in urls)
    assert any(url.endswith(".css") for url in urls)
    for url in urls:
        assert assets.get(url.lstrip("/")), f"Missing {url} in {path.name}"
    print(f"{path.name}: {len(files)} files, {len(assets)} console assets, no local data")
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory.resolve()
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Use a directory with one wheel and one sdist"
    assert check_contents(wheels[0]) == check_contents(sources[0]), "Console assets differ"
    with TemporaryDirectory(prefix="quitbot-wheel-check-") as temporary:
        subprocess.run(
            [
                "uv", "run", "--no-project", "--isolated", "--python", sys.executable,
                "--with", str(wheels[0]), "--with", f"httpx=={version('httpx')}",
                "python", "-I", "-c", SMOKE_TEST,
            ],
            cwd=temporary,
            check=True,
        )


if __name__ == "__main__":
    main()
