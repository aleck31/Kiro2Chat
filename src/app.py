"""kiro2chat - Main application entry point."""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

# Ensure non-interactive tool execution (must be set before any imports that read them)
os.environ.setdefault("AWS_PAGER", "")
os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import config
from .core import TokenManager
from .core.client import KiroClient
from .core.health import check_health
from .api.routes import router, init_services
from .api.anthropic_routes import router as anthropic_router, init_anthropic_routes
from .api.agent_routes import router as agent_router, init_agent_routes

# Configure logging
from logging.handlers import RotatingFileHandler
from .log_context import UserTagFilter

_log_fmt = "%(asctime)s [%(levelname)s] %(name)s%(user_tag)s: %(message)s"
_user_filter = UserTagFilter()

# Console handler — follows LOG_LEVEL
_console = logging.StreamHandler()
_console.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
_console.setFormatter(logging.Formatter(_log_fmt))
_console.addFilter(_user_filter)

# File handler — always DEBUG, 20MB × 10 files
_log_dir = config.data_dir / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_file = RotatingFileHandler(
    _log_dir / "kiro2chat.log", maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8",
)
_file.setLevel(logging.DEBUG)
_file.setFormatter(logging.Formatter(_log_fmt))
_file.addFilter(_user_filter)

logging.basicConfig(level=logging.DEBUG, handlers=[_console, _file])
# Suppress overly verbose third-party debug logs
logging.getLogger("openai._base_client").setLevel(logging.INFO)     # drops per-request options dump
logging.getLogger("strands.models.openai").setLevel(logging.INFO)   # drops "formatted request" dump
logging.getLogger("strands.tools.registry").setLevel(logging.WARNING)  # drops per-tool "loaded tool config" x30
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)  # suppress SSE reconnect spam
logging.getLogger("httpx").setLevel(logging.WARNING)                # suppress MCP HTTP request logs
logging.getLogger("httpcore").setLevel(logging.INFO)                # drops GeneratorExit false-failure noise
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: init and cleanup."""
    tm = TokenManager()
    kiro = KiroClient()
    init_services(tm, kiro)
    init_anthropic_routes(tm, kiro)
    global _health_tm, _health_cw
    _health_tm, _health_cw = tm, kiro

    try:
        await tm.get_access_token()
        logger.info(f"✅ Token valid, profile: {tm.profile_arn}")
    except Exception as e:
        logger.error(f"❌ Token validation failed: {e}")
        logger.error("Make sure kiro-cli is logged in: kiro-cli login")

    # Initialize Strands Agent
    mcp_clients = []
    try:
        from .agent import create_agent, get_enabled_servers
        servers = get_enabled_servers()
        agent, mcp_clients = create_agent(servers=servers)
        init_agent_routes(agent, mcp_clients, servers)
        logger.info("✅ Strands Agent initialized")
    except Exception as e:
        logger.warning(f"⚠️ Agent init skipped: {e}")

    yield

    # Cleanup MCP clients
    for client in mcp_clients:
        try:
            client.stop(None, None, None)
        except Exception:
            pass
    await tm.close()
    await kiro.close()


app = FastAPI(
    title="kiro2chat",
    description="Kiro to Chat - OpenAI-compatible API powered by Kiro/CodeWhisperer",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(anthropic_router)
app.include_router(agent_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store references for health check
_health_tm: TokenManager | None = None
_health_cw: KiroClient | None = None


@app.get("/")
async def root():
    return {
        "name": "kiro2chat",
        "version": __version__,
        "status": "running",
        "endpoints": {
            "models": "/v1/models",
            "chat": "/v1/chat/completions",
            "messages": "/v1/messages",
            "agent_chat": "/v1/agent/chat",
            "agent_tools": "/v1/agent/tools",
            "health": "/health",
            "metrics": "/metrics",
        },
    }


@app.get("/health")
async def health():
    if _health_tm and _health_cw:
        return await check_health(_health_tm, _health_cw)
    return {"status": "starting", "checks": {}}


@app.get("/metrics")
async def metrics():
    from .metrics import get_metrics, get_content_type
    from fastapi.responses import Response
    return Response(content=get_metrics(), media_type=get_content_type())


def run_api():
    """Run the API server."""
    uvicorn.run(
        "src.app:app",
        host=config.host,
        port=config.port,
        log_level=config.log_level,
    )


def run_webui():
    """Run the Gradio Web UI."""
    from .webui import create_ui, LAUNCH_KWARGS
    demo = create_ui()
    demo.launch(**LAUNCH_KWARGS)


def run_bot():
    """Run the Telegram bot (legacy loopback mode)."""
    from .bot.telegram import run_bot as _run_bot
    asyncio.run(_run_bot())


def run_acp_bot():
    """Run Telegram bot via ACP bridge (new mode)."""
    from .acp.bridge import Bridge
    from .adapters.telegram import TelegramAdapter, get_bot_token

    token = get_bot_token()
    if not token:
        logger.error("TG_BOT_TOKEN not set")
        sys.exit(1)

    bridge = Bridge(
        cli_path=config.kiro_cli_path,
        workspace_mode=config.workspace_mode,
        working_dir=config.working_dir,
        idle_timeout=config.idle_timeout,
    )
    bridge.start()

    adapter = TelegramAdapter(bridge, token)
    try:
        asyncio.run(adapter.start())
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


def run_all():
    """Run API + WebUI + Bot together."""
    import threading
    from .bot.telegram import get_bot_token

    # Start API in a thread
    api_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": "src.app:app",
            "host": config.host,
            "port": config.port,
            "log_level": config.log_level,
        },
        daemon=True,
    )
    api_thread.start()
    logger.info("🚀 API server starting on port %d", config.port)

    # Start bot in a thread if token is available (no signal handling in sub-thread)
    if get_bot_token():
        def _run_bot_no_signals():
            from .bot.telegram import run_bot as _run_bot
            asyncio.run(_run_bot(handle_signals=False))

        bot_thread = threading.Thread(target=_run_bot_no_signals, daemon=True)
        bot_thread.start()
        logger.info("🤖 Telegram bot starting")
    else:
        logger.info("⏭️ TG_BOT_TOKEN not set, skipping Telegram bot")

    # Run WebUI in main thread (blocking)
    logger.info("🌐 Web UI starting on port 7860")
    run_webui()


USAGE = """\
Usage: kiro2chat <action> [service]

Actions (background management via tmux):
  start   [service]  Start service(s) in background
  stop    [service]  Stop service(s)
  restart [service]  Restart service(s)
  status  [service]  Show running status
  attach  [service]  Attach to tmux session (Ctrl+B D to detach)

Services (optional, default: all):
  api     API server (port 8000)
  webui   Gradio Web UI (port 7860)
  bot     Telegram Bot

Direct run (foreground):
  kiro2chat api|webui|bot|all

Options:
  -h, --help  Show this help
"""

_SERVICE_PORTS = {
    "all":   "api:8000 webui:7860",
    "api":   "8000",
    "webui": "7860",
    "bot":   None,
}

_TMUX_SESSIONS = {
    "all":   ("kiro2chat",        "uv run kiro2chat all"),
    "api":   ("kiro2chat-api",    "uv run kiro2chat api"),
    "webui": ("kiro2chat-webui",  "uv run kiro2chat webui"),
    "bot":   ("kiro2chat-bot",    "uv run kiro2chat bot"),
}


def _tmux_running(session: str) -> bool:
    import subprocess
    r = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True)
    return r.returncode == 0


def _tmux_start(session: str, cmd: str):
    import subprocess
    from pathlib import Path
    cwd = str(Path(__file__).parent.parent)
    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", cwd, cmd], check=True)
    print(f"Started (tmux session: {session})")


def _tmux_stop(session: str):
    import subprocess
    subprocess.run(["tmux", "kill-session", "-t", session], check=True)
    print(f"Stopped (tmux session: {session})")


def _handle_bg(service: str, action: str):
    session, cmd = _TMUX_SESSIONS[service]
    if action == "start":
        if _tmux_running(session):
            print(f"Already running (tmux session: {session})")
            sys.exit(1)
        _tmux_start(session, cmd)
    elif action == "stop":
        if not _tmux_running(session):
            print("Not running")
            sys.exit(1)
        _tmux_stop(session)
    elif action == "restart":
        if _tmux_running(session):
            _tmux_stop(session)
            import time
            time.sleep(1)
        _tmux_start(session, cmd)
    elif action == "status":
        if not _tmux_running(session):
            print(f"{session}: stopped")
        else:
            import subprocess
            pid = subprocess.run(
                ["tmux", "list-panes", "-t", session, "-F", "#{pane_pid}"],
                capture_output=True, text=True
            ).stdout.strip()
            etime = ""
            if pid:
                r = subprocess.run(
                    ["ps", "-o", "etime=", "-p", pid],
                    capture_output=True, text=True
                )
                etime = r.stdout.strip()
            ports = _SERVICE_PORTS.get(service)
            lines = [f"{session}: running"]
            if etime:
                lines.append(f"  uptime: {etime}")
            if pid:
                lines.append(f"  pid:    {pid}")
            if ports:
                lines.append(f"  ports:  {ports}")
            print("\n".join(lines))
    elif action == "attach":
        import os
        os.execvp("tmux", ["tmux", "attach", "-t", session])
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


def _handle_daemon(action: str):
    print("daemon install/uninstall is not supported. Please refer to docs/DEPLOYMENT.md for systemd setup.")
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    # daemon install/uninstall
    if args[0] == "daemon":
        action = args[1] if len(args) > 1 else ""
        _handle_daemon(action)
        return

    _BG_ACTIONS = {"start", "stop", "restart", "status", "attach"}
    _SERVICES = {"api", "webui", "bot", "all"}

    # kiro2chat <action> [service]
    if args[0] in _BG_ACTIONS:
        service = args[1] if len(args) > 1 and args[1] in _SERVICES else "all"
        _handle_bg(service, args[0])
        return

    # kiro2chat <service> <action>  (legacy, keep for compatibility)
    if args[0] in _SERVICES and len(args) > 1 and args[1] in _BG_ACTIONS:
        _handle_bg(args[0], args[1])
        return

    # foreground run
    cmd = args[0]
    if cmd == "api":
        run_api()
    elif cmd == "webui":
        run_webui()
    elif cmd == "bot":
        run_acp_bot()
    elif cmd == "all":
        run_all()
    else:
        print(f"Unknown command: {cmd}\n")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
