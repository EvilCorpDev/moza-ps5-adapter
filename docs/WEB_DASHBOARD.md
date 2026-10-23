# Pit Control

Pit Control runs on the Pi at **`http://PI_LAN_IP:8080/`**, using Python's standard library and static HTML/CSS/JavaScript. It provides Start/Stop, bridge/R5/PS5 status, uptime, a live console and recent-log export. Keyboard shortcuts: **S** starts, **X** stops.

## Setup and everyday use

First follow the [README](../README.md#setup): prepare `~/moza-ps5-current`, configure device permissions, flash v3 output firmware, and edit `tools/start_bridge_current.sh` for your hardware. It is a saved 25%/damper/LED profile, not automatic device discovery. Set the `R5_INPUT` detection path in `dashboard/controller.py` too.

Create root `start-bridge.sh` from that template, then install from the checkout:

```sh
bash deploy/install_dashboard.sh
```

The installer copies the source launcher into the root, so retain your edits in **the template too**. It checks the shared lock, preserves the first pre-install launcher backup and enables the user service. If lingering needs authorization on your OS, run `sudo loginctl enable-linger "$USER"` once and retry. The service is installed for the current user, not root.

1. Power the R5; leave Pico USB unplugged from PS5.
2. Press **Start Bridge**, then connect Pico and select your user.
3. Confirm wheel control in ACC after using gamepad menus.
4. Park before pressing **Stop**.

Closing the browser leaves the session running. Dashboard startup alone does not start force. R5 device detection and live input are separate; PS5/link telemetry is shown only after the bridge reports it, and stale values become unknown.

## Service maintenance

```sh
systemctl --user status moza-ps5-dashboard.service
systemctl --user restart moza-ps5-dashboard.service
systemctl --user stop moza-ps5-dashboard.service
journalctl --user -u moza-ps5-dashboard.service -n 50
loginctl show-user "$USER" -p Linger
```

Unit: `~/.config/systemd/user/moza-ps5-dashboard.service`. Working directory: `~/moza-ps5-current`. Lingering allows it to start at boot without an SSH login. The provided unit listens on all interfaces at port 8080.

Stop/restart requests bridge shutdown: SIGINT, then bounded TERM/KILL fallback. A Linux parent-death guard kills the managed bridge if the dashboard parent crashes; systemd also manages cleanup. Linux force effects retain their finite lifetimes.

The saved launcher uses one `.bridge.lock` shared with browser starts. Another saved launcher exits 75. An externally running bridge shows **EXTERNAL** and must be stopped in its terminal. Direct raw CLI starts do not participate in this launcher lock.

For updates, park, stop the bridge, then stop the dashboard before replacing Python sources. Keep local launcher/path edits. Reinstalling copies the template again. Static-only HTML/CSS/JS edits can be served on the next browser reload without restarting Python.

## Network scope

HTTP controls have Host, same-origin and request-token checks, fixed asset routes and fixed actions. A request cannot supply a shell command or gain override. There is **no account login or TLS**; anyone who can reach the LAN listener can operate it. Keep it on a trusted LAN and do not forward port 8080 to the internet.

The browser shows a saved 25% profile. These labels are fixed UI/controller metadata, not values read from hardware or the launcher's arguments. Update them if you change the saved profile.

## HTTPS hostname through Cloudflare Tunnel

The server rejects unconfigured Host headers by default. A tunnel also changes the browser's origin from local HTTP to public HTTPS, so changing the Host alone is insufficient for Start/Stop.

First protect the hostname with [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/) and an Allow policy for the intended users. A tunnel alone is not login protection. Have `cloudflared` validate Access tokens using **Protect with Access** where applicable. Host/origin/token checks in this app do not replace authentication.

Keep the tunnel's service pointing to the Pi dashboard, for example `http://192.168.50.62:8080` if the connector is on another LAN machine, or `http://127.0.0.1:8080` if it runs on the Pi. Preserve the browser Host header; do not rewrite it to localhost.

Configure the exact public origin on the Pi:

```sh
mkdir -p ~/.config/moza-ps5
nano ~/.config/moza-ps5/dashboard.env
```

Add, substituting your hostname:

```ini
PIT_CONTROL_PUBLIC_ORIGINS=https://wheelpi.example.com
```

After installing the updated unit, park and stop the bridge, then restart the dashboard:

```sh
systemctl --user daemon-reload
systemctl --user restart moza-ps5-dashboard.service
```

The optional environment file is user-local and is not copied into the repository. Separate multiple origins with spaces inside a quoted environment value. For manual server launches, `--public-origin https://wheelpi.example.com` is repeatable. Only explicit HTTPS origins are accepted; no wildcard hosts or URL paths are supported.

The server matches the configured public authority and requires its exact HTTPS Origin plus the existing request token for control actions. The external HTTPS port may differ from the Pi's HTTP port. It does not trust `X-Forwarded-Host`, `X-Forwarded-Proto` or `Forwarded` to grant access. Existing local hostname/IP access on the listening port remains available.

Verify the browser page and console connect at the HTTPS URL. Check control requests with a simulated bridge when testing changes; do not use a production Start action merely as a connectivity probe. Confirm that an unauthenticated external browser is intercepted by Access before enabling remote physical controls.

## Logs

- Browser: latest 180 concise messages; Export downloads that recent view.
- Full managed stdout/stderr: `logs/dashboard-bridge.log`, rotating at 5 MiB with four backups, up to 25 MiB for that log family.
- Dashboard/service failures: systemd journal.
- First launcher backup: `.maintenance/start-bridge.before-dashboard.sh`.

Logs/backups are installation-local and ignored by Git. A raw bridge log may contain device identifiers and authentication exchanges; inspect it before sharing.

## Validation

`dashboard/tests/test_dashboard.py` uses dummy processes and HTTP clients to test concurrent starts, bounded shutdown/escalation, failures, stale telemetry, external locks, request validation and parent-death cleanup. The real saved launcher is exercised with a fake `moza_ps5` module on Linux. It sends no hardware output.

These tests were also run on the Pi. The dashboard's browser controls were checked with a simulated bridge. Enabling the service and inspecting its configuration is distinct from verifying an actual boot or physical driving session.
