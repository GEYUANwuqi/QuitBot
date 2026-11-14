"""Start management and per-account listeners with live configuration."""

import asyncio
import logging
import os
import platform
import sys
from pathlib import Path

from . import __version__
from .diagnostics import configure_logging


async def main(path, logs):
    # Logging is ready before importing the native adapters or reading config.
    from .auth import AccessKeys
    from .config import ConfigStore, WebUIConfig
    from .management import create_management_app
    from .service import Service, bound_socket, server_for

    loop = asyncio.get_running_loop()

    def unhandled_task(loop, context):
        error = context.get("exception")
        logging.getLogger(__name__).error(
            "Background task failed: %s",
            context.get("message"),
            exc_info=(type(error), error, error.__traceback__) if error else None,
        )

    loop.set_exception_handler(unhandled_task)
    store = ConfigStore(path)
    logs.configure(
        store.current.logging.retention_days,
        (
            endpoint.access_token
            for account in store.current.accounts
            for endpoint in (account.forward_ws, account.reverse_ws)
        ),
    )
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
        logs=logs,
    )
    service.validate(store.current)
    server = server_for(create_management_app(service), webui.host, webui.port)
    print(f"QuitBot console: {service.snapshot()['webui']['url']}", flush=True)
    print(f"Configuration: {path}", flush=True)
    print(f"Logs: {logs.directory}", flush=True)
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
    path = Path(os.environ.get("QUITBOT_CONFIG", "config/accounts.json")).resolve()
    logs = configure_logging(Path.cwd() / "logs")
    try:
        logging.getLogger(__name__).info(
            "Starting QuitBot version=%s platform=%s python=%s",
            __version__,
            platform.platform(),
            sys.version.split()[0],
        )
        asyncio.run(main(path, logs))
    except KeyboardInterrupt:
        print("QuitBot 已退出。", flush=True)
    except Exception:
        logging.getLogger(__name__).exception("QuitBot startup or service failure")
        raise SystemExit(1) from None
    finally:
        logging.getLogger(__name__).info("QuitBot stopped")
        logging.getLogger().removeHandler(logs)
        logs.close()
