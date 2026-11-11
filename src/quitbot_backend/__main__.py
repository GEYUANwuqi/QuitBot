"""Start management and per-account listeners with live configuration."""

import asyncio
import logging
import os
from pathlib import Path

from .auth import AccessKeys
from .config import ConfigStore, WebUIConfig
from .management import create_management_app
from .service import Service, bound_socket, server_for


async def main():
    path = Path(os.environ.get("QUITBOT_CONFIG", "config/accounts.json")).resolve()
    store = ConfigStore(path)
    override = os.environ.get("QUITBOT_MANAGEMENT_PORT")
    webui = WebUIConfig.model_validate(
        {
            **store.current.webui.model_dump(),
            **({"port": int(override)} if override else {}),
        }
    )
    listener = bound_socket(webui.host, webui.port)
    access = AccessKeys(path.parent / "security.json")
    service = Service(
        store,
        access,
        management_port=webui.port,
        management_host=webui.host,
        port_overridden=bool(override),
    )
    service.validate(store.current)
    server = server_for(create_management_app(service), webui.host, webui.port)
    print(f"QuitBot console: {service.snapshot()['webui']['url']}", flush=True)
    print(f"Configuration: {path}", flush=True)
    if access.initial_password:
        print(
            f"Initial password (change after login): {access.initial_password}",
            flush=True,
        )
    watcher = asyncio.create_task(service.watch())
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await asyncio.shield(serving)
    finally:
        service.stopping.set()
        await asyncio.gather(watcher, return_exceptions=True)
        await service.close()
        server.should_exit = True
        await asyncio.gather(serving, return_exceptions=True)
        listener.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("QuitBot 已退出。", flush=True)
