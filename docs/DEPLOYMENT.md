# Deployment Guide

Complements the Quick Start in the main [README](../README.md) — this
document focuses on **platform-specific bot setup**, **production
operation**, and **data layout**.

## Platform Setup

### Telegram

1. Message [@BotFather](https://t.me/BotFather) on Telegram.
2. `/newbot` → follow prompts → get Bot Token.
3. Paste the token into the Telegram tab at `/settings`, or set
   `tg_bot_token` in `config.toml`.

### Lark / Feishu

1. Go to [Lark Open Platform](https://open.larksuite.com/app) (international)
   or [飞书开放平台](https://open.feishu.cn/app) (China).
2. Create an **Enterprise App** (企业自建应用).
3. Grab **App ID** and **App Secret** from the app credentials page.
4. Enable permissions (Tenant token scopes):
   - `im:message` — read/send direct & group messages
   - `im:message.p2p_msg:readonly` — receive DMs sent to the bot
   - `im:message.group_at_msg` — receive @bot messages in groups (if available)
   - `im:resource` — download images/files from messages
   - `contact:contact.base:readonly` — call `contact.v3.user.get` (needed to resolve display names for the authorization allowlist)
   - `contact:user.base:readonly` — read the `name` field on the user object returned by the call above
5. Subscribe to events:
   - `im.message.receive_v1` — delivery method **WebSocket** (长连接)
6. Publish the app (发布应用).
7. Configure `lark_app_id`, `lark_app_secret`, and `lark_domain` (`lark`
   for international, `feishu` for China) via `/settings` or `config.toml`.

### Discord

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create application → Bot → copy the Bot Token.
3. Enable **Privileged Gateway Intents** → **Message Content Intent**.
4. Bot Permissions: Send Messages, Read Message History, Attach Files.
5. Invite the bot with an OAuth2 URL (scope: `bot`, plus the permissions above).
6. Paste the token into the Discord tab at `/settings`.

## Authorization (Allowlist + Claim Tokens)

Each bot talks to `kiro-cli`, which effectively gives the caller shell access on the host. Every adapter therefore has an allowlist gated by a `Require authorization` switch in `/settings` → **Adapters**:

- **Telegram** — on by default (bot handles are public once discovered).
- **Lark / Discord** — off by default; turn on to restrict access.

With authorization on, only users on the allowlist can interact with the bot. New users join the allowlist via a one-time **claim token**:

1. Operator opens `/settings` → adapter section → click **Generate claim token** (valid 15 min).
2. User DMs the bot: `/claim <token>`.
3. Bot replies `✅ Authorized`. The user's id (and display name, if the platform exposes it) is written to `config.toml` and takes effect immediately — no restart.

The `Require authorization` toggle itself and per-user revocations from the list also apply instantly.

## Scheduled Tasks (Heartbeat)

`/settings` → **Heartbeat** lets you schedule a prompt that runs through
the ACP bridge on a fixed cadence and pushes the answer to one adapter.

Each task picks a schedule (interval like "every 2 hours", or a cron
expression), a workspace, a prompt, and a target (telegram / lark /
discord / webchat). Empty `target_chat_ids` means broadcast — to the
adapter's allowlist for bots, or to every open `/chat` tab for webchat.

If a workspace's ACP session is busy (a user is chatting), the scheduled
run queues and fires as soon as the session is free, so broadcasts are
never silently dropped.

Saving the tab restarts the scheduler; individual tasks can be fired
out-of-schedule via the ▶ button.

**Recommended: give scheduled tasks their own workspace.** ACP sessions
are keyed by workspace, so a task sharing a workspace with a human chat
will land its prompts and answers in the same session history. That is
sometimes what you want (e.g. a morning summary that references "what
we discussed yesterday"); if you'd rather keep scheduled runs from
bleeding into your chat context, create a dedicated workspace (e.g.
`tasks-daily`) for them.

## Running as a systemd User Service

The README covers install (`kiro2chat install`) and day-to-day commands
(`start` / `stop` / `restart` / `status`). A few production-grade extras:

### Live logs

```bash
journalctl --user -u kiro2chat -f
```

### Keep running after logout

systemd user services stop when you log out, unless lingering is on:

```bash
sudo loginctl enable-linger $(whoami)
```

### Uninstall

```bash
kiro2chat uninstall
```

Stops the service, disables it, and removes the unit file.

## Data Directories

| Path | Purpose |
|------|---------|
| `~/.config/kiro2chat/config.toml` | Configuration (tokens, ACP params, workspaces) |
| `~/.local/share/kiro2chat/logs/` | Application logs (rotating, 20 MB × 10) |
| `~/.local/share/kiro2chat/workspaces/` | Default per-chat workspace directories |
| `~/.kiro/sessions/cli/` | ACP session files (`.json` + `.jsonl` + `.lock`) |

## Security Notes

- `config.toml` holds bot tokens in plaintext. Lock it down:
  ```bash
  chmod 600 ~/.config/kiro2chat/config.toml
  ```
- The web dashboard listens on `127.0.0.1` by default — local access only.
- To expose it externally, keep `web_host = "127.0.0.1"` and put a reverse
  proxy with TLS (nginx / Caddy / Traefik) in front, forwarding to
  `127.0.0.1:7860`. NiceGUI does not ship its own TLS terminator, and the proxy
  must forward WebSocket upgrade headers (NiceGUI relies on a WS connection).
- The dashboard controls an agent that can run commands and edit files, so
  **never expose it unauthenticated**. Either enable the built-in Cognito SSO
  (below) or gate it at the proxy (HTTP basic auth / oauth2-proxy / network ACLs).

## Dashboard authentication (Cognito OIDC)

Optional, off by default. When enabled, every dashboard page requires a login
through your Cognito Hosted UI (Authorization Code flow). Configure under
`[auth]` in `config.toml`:

```toml
[auth]
enabled = true
cognito_region = "ap-southeast-1"
cognito_user_pool_id = "ap-southeast-1_xxxxxxxxx"
cognito_client_id = "xxxxxxxxxxxxxxxxxxxxxxxxxx"
cognito_client_secret = "xxx"
cognito_domain = "your-hosted-ui-domain"     # the prefix only
base_url = "https://kiro.example.com"         # public URL of the dashboard
# allowed_emails = "a@x.com,b@x.com"          # optional allowlist; empty = any pool user
```

Setup steps:

1. Create a **dedicated app client** in your user pool (confidential, i.e. with a
   client secret). Enable the **Authorization code grant** and scopes
   `openid email profile`.
2. Set its **Allowed callback URL** to `<base_url>/auth/callback` and
   **Allowed sign-out URL** to `<base_url>/`. For local testing you can also add
   `http://localhost:7860/auth/callback`.
3. If your Hosted UI domain uses **Managed Login (branding v2)**, every app
   client needs a branding style or the login page shows
   *"Login pages unavailable"*. Create a default one:
   ```bash
   aws cognito-idp create-managed-login-branding \
     --user-pool-id <pool-id> --client-id <client-id> --use-cognito-provided-values
   ```
4. `base_url` must exactly match the host users type in the browser (and the
   registered callback). `localhost` and `127.0.0.1` are different cookie
   origins — mixing them causes `mismatching_state` on callback.
5. Put TLS in front and set `base_url = https://<your-domain>`.
