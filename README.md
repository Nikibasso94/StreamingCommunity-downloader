<p align="center">
  <img src="docs/banner.png" alt="StreamingCommunity Downloader" width="720"/>
</p>

<p align="center">
  A self-hosted web panel to download films, TV series and anime into a Jellyfin library.<br/>
  Request queue, followed series, notifications to Discord and Telegram, and an integrated
  file manager.
</p>

---

## Screenshots

<p align="center">
  <img src="docs/home.png" alt="Start page" width="90%"/>
</p>
<p align="center">
  <sub><b>The start page</b> — the source's own shelves, browsable before you have searched for
  anything.</sub>
</p>

### Finding something

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/search.png" alt="Search results"/><br/>
      <sub><b>Search</b> — films, series and anime across both sources, with the state of each
      title readable on the card.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/film-detail.png" alt="Film page"/><br/>
      <sub><b>Film page</b> — plot, genres, rating, artwork and trailer, taken from the title page
      itself. No API key involved.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/serie-detail.png" alt="Series page"/><br/>
      <sub><b>Series page</b> — seasons and episodes, with the audio and subtitle tracks each one
      actually offers.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/downloads.png" alt="Downloads"/><br/>
      <sub><b>Downloads</b> — live progress per phase (video → audio → merge), as one list rather
      than a wall of cards.</sub>
    </td>
  </tr>
</table>

### Asking, approving, following

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/request.png" alt="My requests"/><br/>
      <sub><b>My requests</b> — what you asked for and how far it got. A request can be withdrawn
      until it is resolved.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/request-approval.png" alt="Request queue"/><br/>
      <sub><b>Request queue</b> — approving re-resolves the title and verifies the tracks first: a
      request that cannot find the audio asked for is parked, never substituted.</sub>
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center" valign="top">
      <img src="docs/series-follow.png" alt="Followed series" width="60%"/><br/>
      <sub><b>Followed series</b> — new episodes are looked for on their own. A followed series
      never downloads by itself: it opens a request, which goes through the queue or is approved
      on the spot, according to the permissions of whoever follows it.</sub>
    </td>
  </tr>
</table>

### Running it

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/settings.png" alt="Settings"/><br/>
      <sub><b>Settings</b> — libraries, performance, naming templates, notification channels and
      download hooks.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/jellyfin-users.png" alt="Users"/><br/>
      <sub><b>Users</b> — imported from Jellyfin, with independent permissions. There is no ADMIN
      super-permission: an administrator who never sees the request queue is a valid setup.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/file-manager.png" alt="File manager"/><br/>
      <sub><b>File manager</b> — drag-and-drop, video streaming in place, and the free space left
      on the media volume.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="docs/login.png" alt="Login"/><br/>
      <sub><b>Login</b> — Jellyfin credentials, so no second account and no local password store.
      Authentication is opt-in; the panel also runs open.</sub>
    </td>
  </tr>
</table>

---

## Features

- Search and download films, TV series, and anime
- **Recovers the source domain on its own** when it rotates: finds the new one, verifies it,
  and proposes it for one click
- **Plot, genres, rating, artwork and trailers** on the title page — no API key required
- Automatic quality selection (1080p → 720p → 480p → 360p)
- Parallel HLS segment download with AES-CBC decryption
- Multi-audio track merge via FFmpeg
- Subtitle download (`.vtt`) for non-Italian audio tracks
- Real-time download progress with per-phase steps (video → audio → merge)
- Integrated file manager with drag-and-drop, video streaming and free space on the media volume
- Jellyfin library path configuration, and an optional **library refresh when a download lands**
- **Plex, Sonarr and Radarr connectors**: the same library refresh, plus an optional sync of
  Sonarr/Radarr's own "wanted" lists into downloads here
- **Post-download webhooks** with a body template
- **Configurable file and folder names**
- Scheduled downloads
- **Follow a series or anime** and get new episodes as the source publishes them
- **Notifications to Discord, Telegram, ntfy and anything else Apprise speaks**, with per-channel
  event selection
- One notification per download — and a single summary for a whole season or series
- Optional login with Jellyfin credentials — no second account, no local password store
- Independent permissions: download directly, request, approve, manage users, manage settings
- Request queue with approval, preserving the audio and subtitle tracks the requester chose
- Request status on the search result cards, and an in-app notification bell
- Docker ready

---

## Quick Start

### Docker (recommended)

```bash
curl -O https://raw.githubusercontent.com/EdoardoFiore/StreamingCommunity-downloader/main/docker-compose.template.yml
# Edit the volume paths — create the config one — then:
docker compose -f docker-compose.template.yml up -d
```

The panel is available at `http://localhost:8000`. Set the source domain in **Impostazioni** on first
use: it is not shipped in the image, because it rotates.

The image is published to GitHub Container Registry on every push to `main`:

```
ghcr.io/edoardofiore/streamingcommunity-downloader:latest
```

### Portainer

The same image works as a Portainer stack: **Stacks → Add stack → Web editor**, paste the compose
below, edit the two `device:` paths to real, already-existing directories on the host, then
**Deploy**.

```yaml
services:
  web:
    image: ghcr.io/edoardofiore/streamingcommunity-downloader:latest
    ports:
      - "8000:8000"
    volumes:
      - nfs_storage:/app/videos
      - panel_config:/app/config
    environment:
      - VIDEOS_DIR=/app/videos
      - DB_FILE=/app/config/panel.db
      - DATA_FILE=/app/config/data.json
      - SCHEDULE_FILE=/app/config/schedule.json
      - HOST=0.0.0.0
      - PORT=8000
      - COOKIE_SECURE=0
      - COOKIE_SAMESITE=lax
      - TRUST_PROXY_HEADERS=0
      # On an NFS/NAS share with root_squash — see "PUID/PGID" below — the
      # videos and config volumes must already be writable by this uid/gid.
      # - PUID=1000
      # - PGID=1000
      # - TZ=Europe/Rome
    dns:
      - 8.8.8.8
      - 1.1.1.1
    restart: unless-stopped
    deploy:
      replicas: 1   # see "Run one process" below — has no effect outside Swarm

volumes:
  nfs_storage:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /srv/nfs/storage   # ← real path for videos
  panel_config:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /srv/scpanel/config   # ← real path for panel.db/data.json; create it first
```

Running your own fork instead? Point `image:` at your own package — `.github/workflows/docker-publish.yml`
builds and publishes it the same way, under `ghcr.io/<your-username>/streamingcommunity-downloader`,
on every push to `main`. Two things only matter on a fork:

- **GitHub disables Actions on forks by default.** If pushing never produces a run under the
  **Actions** tab, open it once and accept "I understand my workflows, go ahead and enable them" —
  a one-time switch, not needed again after that.
- **GHCR packages are created private.** Portainer cannot pull a private image with no credentials
  configured, so make the package public from `github.com/<your-username>?tab=packages` →
  the package → **Package settings** → **Change visibility** → **Public** — or add registry
  credentials in Portainer instead.

### Trying a branch before it is released

Every push to a branch other than `main` publishes to a **separate** package, so a work-in-progress
build can never be pulled by a deployment pointing at the release image:

```
ghcr.io/edoardofiore/streamingcommunity-downloader-dev:dev          # the latest branch build
ghcr.io/edoardofiore/streamingcommunity-downloader-dev:my-branch    # that branch only
ghcr.io/edoardofiore/streamingcommunity-downloader-dev:sha-abc1234  # one exact commit
```

Tests have to pass first: a red `pytest -q` publishes nothing.

```bash
curl -O https://raw.githubusercontent.com/EdoardoFiore/StreamingCommunity-downloader/main/docker-compose.dev.template.yml
# Point panel_config_dev at a NEW directory, then:
docker compose -f docker-compose.dev.template.yml up -d
```

It listens on `http://localhost:8001` so it can run alongside the release panel. Give it its own
config directory: **database migrations only run forwards**, so pointing a dev image at the
production config volume upgrades the real database with no way back.

### From source

```bash
git clone https://github.com/EdoardoFiore/StreamingCommunity-downloader.git
cd StreamingCommunity-downloader
pip install -r requirements.txt
python main.py

pip install -r requirements-dev.txt   # tests only
pytest -q
```

**Prerequisites:** Python ≥ 3.11, FFmpeg.

### Upgrading from v1

v1 is the panel before the Jellyfin login existed. **Pulling the new image changes nothing by
default**: a panel that was running without a login keeps running without one. You will see the
redesigned interface, and you have to set the source domain once (see above).

To turn the login on, copy the `/app/config` volume and the `DB_FILE` / `DATA_FILE` /
`SCHEDULE_FILE` variables from `docker-compose.template.yml`, then connect Jellyfin from
**Impostazioni → Accesso e utenti**. That volume is not optional: users, sessions and requests live
in `panel.db`, which without it sits inside the container and is lost on every pull — including the
answer to the setup question, which would then be asked again on every restart.

To stay on v1, pin the tag instead of following `latest`:

```yaml
image: ghcr.io/edoardofiore/streamingcommunity-downloader:1.0.0
```

If you were running the `-jellyfin` image from the development branch, change the `image:` line to
the main one above — that package is frozen and no longer built. Nothing else changes: the variables
and the `/app/config` volume are the same, so `panel.db` and your users carry over as they are.

---

## Users, roles and requests

On first run the panel asks once, on its setup screen, whether to use Jellyfin. Choosing **Collega a
Jellyfin** configures the panel and creates its administrator from that sign-in; only a Jellyfin
administrator can do it. After that, Jellyfin accounts **do not** get access automatically —
an administrator imports them from **Utenti** and assigns permissions. Opening the panel to every
Jellyfin account is a switch on that page, off by default.

Permissions are independent flags, not a ladder:

| Permission | Grants |
|---|---|
| `DOWNLOAD` | start and schedule downloads directly |
| `REQUEST` | search and create requests |
| `MANAGE_REQUESTS` | see the queue, approve, deny, fix |
| `MANAGE_USERS` | import users, assign permissions, disable accounts |
| `MANAGE_SETTINGS` | source domain, libraries, performance |
| `MANAGE_FILES` | move, rename and delete in the file manager |
| `VIEW_LIBRARY` | browse and stream the library |

There is deliberately no "admin" flag that implies the rest, so you can have an administrator who
manages settings and users but never sees the request queue.

A user without `DOWNLOAD` sees the same form and the same audio and subtitle checkboxes; the button
says **Richiedi** and creates a request. On approval the source is re-resolved against the current
domain — a dead link or a missing audio track parks the request for a human instead of downloading
the wrong thing.

Two people asking for the same content with the same tracks share one download and are both
notified. Asking for **different** tracks makes two distinct requests, because merging them would
give one of them the wrong file — but they resolve to the same path in the library, so the download
fetches the union of what everyone asked for. Two people wanting one film in Italian and English get
one file carrying both, and the player picks. A request whose tracks are already in the file is
satisfied without downloading anything: the check reads the file's audio and subtitle streams, not
its name.

### Running without Jellyfin

Press **Continua senza Jellyfin** on the setup screen and the panel runs with no login at all: every
visitor gets direct download, settings and the file manager. You can connect Jellyfin later from
**Impostazioni → Accesso e utenti**, without a restart.

There is no environment variable for this. It used to be `AUTH_ENABLED`, which is gone: set to `0`
it did not just default to open mode, it hid the setup screen altogether, so the choice the panel
offers could not be made. An existing deployment that was running without it keeps open mode across
the upgrade — the panel writes that answer into `panel.db` the first time it starts.

Going back — from a connected Jellyfin to no login — is deliberately not offered in the UI: the
imported users and their permissions would be left in an ambiguous state.

---

## Following a series

Any series or anime can be **followed** from its page. From then on the panel checks the source
periodically and picks up new episodes as they are published. Following means *from here on*:
everything already released is recorded as seen, so you do not get eight seasons queued the next
morning.

What happens to a new episode depends on the permissions of whoever follows it:

- someone who **can download** finds it in the library on its own;
- someone who **can only request** produces an ordinary request for an approver to accept.

In the second case the approver is told **when the series is followed**, not weeks later when an
episode finally appears, and can **approve the series once**: from then on its new episodes go
straight through, and only that series is affected. The choice is on the *Serie seguite* page, or on
the "Auto i prossimi" checkbox of the request itself.

A watch never downloads anything by itself — it creates a normal request and lets the queue, the
library check and the notifications do their work, so nothing is duplicated and nothing is silently
substituted. **Controlla ora** forces a check immediately instead of waiting for the next cycle; the
interval is in **Impostazioni → Download**.

The *Serie seguite* page lists what you follow, when it was last checked, and whether it downloads
automatically or goes through the queue. An approver also sees everyone else's, with who follows
each one.

---

## Notifications

The panel notifies in two directions.

**In-app**, the bell holds request and download events for the signed-in user, and they can be
marked read or deleted, one at a time or all at once. On a panel running without Jellyfin the bell
belongs to the panel itself, so an open installation still sees its own downloads.

**Outward**, any number of channels can be configured in **Impostazioni → Notifiche** by pasting an
[Apprise](https://github.com/caronc/apprise/wiki) URL — Discord, Telegram, ntfy, Gotify, Slack,
email, and everything else Apprise supports. No key in the compose file, no restart. Each channel
chooses **which events it wants**; selecting none means all of them. Messages carry a title and are
coloured by outcome where the service supports it, and errors have their query strings stripped
before they leave the panel, since external channels are somebody else's servers.

Channels are **global, not per user**: whoever configures one decides what reaches everybody reading
it.

Download notifications are grouped by the action that caused them — one for a film or a single
episode, and a **single summary** for a whole season, series or anime, listing any episodes that
failed and why.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | bind address |
| `VIDEOS_DIR` | `videos` | download destination |
| `DB_FILE` | `panel.db` | users, sessions, requests — **put this on a persistent volume** |
| `DATA_FILE` | `data.json` | source domain, libraries, performance settings |
| `SCHEDULE_FILE` | `schedule.json` | scheduled downloads |
| `TMP_DIR` | `tmp` | HLS segments while a job runs, cleaned up afterwards |
| `FFMPEG_PATH` | — | full path to `ffmpeg`, when it is not on `PATH` |
| `FFPROBE_PATH` | — | full path to `ffprobe`; see the note below |
| `DOMAIN_SOURCE_URL` | a public page | where replacement domains are read from |
| `DOMAIN_NAME_PATTERN` | `streaming(community\|unity)…` | which names may be adopted automatically |
| `COOKIE_SECURE` | `0` | set to `1` when serving over HTTPS |
| `COOKIE_SAMESITE` | `lax` | `none` (with `COOKIE_SECURE=1`) only to embed the panel cross-site |
| `TRUST_PROXY_HEADERS` | `0` | set to `1` only behind a reverse proxy you control |
| `PUID` / `PGID` | unset (root) | run as this user/group instead of root — see below |
| `TZ` | unset (UTC) | e.g. `Europe/Rome`, for log timestamps |

The source domain and the Jellyfin library paths are configurable from **Impostazioni** in the UI.

**PUID/PGID.** Unset, the container runs as root exactly as it always has — this changes nothing
for an existing deployment. Set both when a mounted volume is not writable by root: the common case
is an NFS export with **root_squash** (the default on most NAS), which maps the container's root to
an unprivileged "nobody" with no write access to that share, even though the same share already
works for every other container that happens to run as a normal user. Set `PUID`/`PGID` to whichever
uid/gid those containers use — both the videos volume **and** the config one need to already be
writable by it, since setting this switches every file the panel writes, not only downloads.

**FFmpeg and ffprobe.** `ffmpeg` is required; the Docker image installs it. Without one on `PATH`
the panel falls back to the static binary bundled with `imageio-ffmpeg`, which is what makes it work
on Windows. `ffprobe` is optional but recommended: it is what lets the panel read which audio and
subtitle tracks a file in the library already carries, so a request for a track that is missing is
not mistaken for one that is already satisfied. Debian's `ffmpeg` package ships both, so the image
has it; where it is absent the panel treats an existing file as satisfying the request and says so
in the log.

`panel.db` holds the Jellyfin service API key: keep it out of any web-served directory and off
world-readable storage.

**Run one process.** Download state is held in memory by a single process, so do not add
`--workers` or scale the service — a second replica would neither see nor report on the first's
downloads.

### Embedding as a Jellyfin custom tab

Point an iframe at the panel; nothing else is required. Sign in once inside the tab and stay signed
in — the session lasts **30 days** and renews on every visit, so in practice you log in once and
forget about it.

With Jellyfin and the panel on subdomains of the same domain over HTTPS — say `jf.example.com` and
`request.example.com` — the frame is same-site and the session cookie is sent inside it with the
default `COOKIE_SAMESITE=lax`. On different domains, or different schemes, the frame is cross-site
and a `Lax` cookie is dropped silently: every request inside the frame looks logged out and login
bounces back on itself, which reads as an infinite loop. Either set `COOKIE_SAMESITE=none` together
with `COOKIE_SECURE=1` — browsers reject a `SameSite=None` cookie that is not also `Secure` — or
reverse-proxy the panel under the same site as Jellyfin.

Chromium browsers separately enforce Private Network Access: the Jellyfin page and the panel must
both resolve to public addresses, or both to private ones, from the browser's point of view. Mixing
tiers fails with "the connection was blocked".

`POST /api/auth/jellyfin-token` trades an already-issued Jellyfin access token for a panel session,
for skipping even that one login. It needs a script running on the Jellyfin page, which posts the
token to the frame. Do not put that script in the custom tab HTML: tab content is injected as a
string, so quoting breaks the page, and `<script>` tags inserted that way never execute.

---

## When the source moves

The source's domain changes every few weeks, and until now that simply stopped the panel: searches
failed and nothing said why. It now notices, reads the current address from a public page, checks
it actually serves the source, and shows a banner offering to switch.

It **proposes**; you apply. That page is edited by people nobody here controls, and the domain
decides where every search, image request and download referer goes — so adopting one because a web
page said so, with nobody looking, is not something the panel does by default. Only second-level
domains whose name matches the expected one are ever considered, and only if they answer like the
source. Anything refused is reported rather than swallowed, because a genuine rebrand and an edited
page look identical from the inside; you can always type a domain in by hand.

**Impostazioni → Sorgente** has a switch for adopting the new domain without asking, off unless you
turn it on, and a "Controlla ora" button.

---

## Metadata

Opening a title shows its plot, genres, rating, backdrop and trailer. All of it comes from the
source's own title page — the same page the panel already reads — so there is **nothing to
configure, no account anywhere, and no request beyond the one already being made**.

TMDB was tried as a second provider and dropped. Measured on real titles it was not an upgrade: the
site copies its synopses from TMDB, so the text was usually identical, while the round trip lost a
trailer on one title, a logo on another and 1100 characters of plot on a third.

Artwork is proxied through the panel, so the browser never talks to anybody else.
Metadata is fetched when you open a title, never for a whole page of search results.

---

## When the stream will not resolve

The video is resolved through the source's embed page, which sits behind
Cloudflare and occasionally refuses. When that happens the title's page now says
so, instead of leaving you with a download button that looks fine and a job that
fails minutes later.

A second route through another provider was built and then removed: the service
it relied on no longer publishes anything usable without reverse-engineering its
internals, which would break silently every time they deploy. The seam it plugged
into is still there, so adding one later is a small change rather than a new
feature.

---

## After a download

**Impostazioni → Hook** can tell something else that a file has landed.

- **Jellyfin**: one switch, using the server already configured for login. The library updates
  immediately instead of on Jellyfin's own schedule.
- **Webhook**: a URL, a method and an optional body, where `{title}`, `{path}`, `{status}`,
  `{type}`, `{season}`, `{episode}`, `{year}` and `{error}` are substituted. With no body a JSON
  object carrying all of them is sent.

**Impostazioni → Integrazioni** adds the same one-switch refresh for **Plex** (URL + token) and for
**Sonarr/Radarr** (URL + API key). For Plex it is a plain rescan, like the Jellyfin switch: it only
confirms a file already in the right place, never moving anything. For Sonarr/Radarr it tries
harder first — see "Sonarr and Radarr" below for what else that tab does, and for what this does
when the download was not started by the sync at all.

With more than one of these switched on for the same download, Sonarr/Radarr always run before
Jellyfin/Plex: Sonarr/Radarr can still move the file into their own library folder at this point,
and a Jellyfin/Plex scan that ran first would look before that move happens, then never look there
again once it does.

There is no "run a command" hook, deliberately: on a panel running without login, settings are open
to every visitor, and a command would hand them a shell. A webhook can point at your own network —
that is how it reaches Jellyfin — so the panel reports only whether the call succeeded, never what
came back.

---

## Sonarr and Radarr

**Impostazioni → Integrazioni** connects the panel to Sonarr and/or Radarr with just a URL and an
API key — no need for both. Besides the post-download rescan described above, each gets its own
switch for syncing what it is missing.

**Syncing the "wanted" list.** With this on, the panel periodically reads Sonarr's or Radarr's own
list of monitored, missing episodes and movies, and tries to download each one here — the same
pipeline a followed series uses, not a separate code path. Sonarr's own list is per-episode; the
panel groups it by series first, so a show missing ten episodes is matched against the source once,
not ten times.

Matching is deliberately conservative, because downloading the wrong film or episode is not a
mistake a retry fixes:

- A **Radarr** movie usually carries a TMDB id. The panel downloads only on an **exact match**
  against the id it already reads from the source's own title page — never a title that merely
  sounds right, even when nothing matches the id.
- A **Sonarr** series carries no TVDB id this source exposes — Sonarr's own first choice — but the
  source does publish an IMDB id, and so does Sonarr's own series record, so the match is just as
  exact whenever Sonarr has one.
- Only when neither side has an id to confirm with does matching fall back to title-and-year
  similarity above a high bar.
- Anything that does not clear that bar — or that had an id with nothing to confirm it against —
  lands in the **"Da verificare"** list on the same tab instead of being guessed at. Search and
  request it by hand from there.

**Excluding a title from the sync.** A film or series you tag in Radarr/Sonarr with the label set
under **Tag da escludere** is skipped entirely — not synced, and exempt from the generic
post-download match below too, even if you download it by hand. Use it for anything you would
rather manage yourself: Radarr/Sonarr's own download client, a different tool, or just by hand.

In **modalità aperta**, a download the sync placed is retried on the next cycle if it actually
fails after being submitted — a dead link, a stalled segment, the container restarting mid-download
— instead of sitting marked as in progress forever with nothing left running it. The same recovery
runs once at startup, for anything a previous run left mid-download when it stopped.

**A manual download gets the same treatment, not only a synced one.** After *any* finished film or
episode — a manual search, a followed series, the sync, it makes no difference — the post-download
refresh checks whether it is something Radarr/Sonarr themselves are missing: a film by the exact
tmdb_id match described above, an episode by the same title-and-year-bar series match plus its
season/episode number. On a hit, the panel moves the file into the folder Radarr/Sonarr *already*
have for that title — read from their own API, nothing configured here — and asks for a rescan
scoped to just that one. No hit just falls back to the plain, unscoped rescan described above.

This is deliberately not Radarr's/Sonarr's "scan a downloads folder and import" command
(`DownloadedMoviesScan`/`DownloadedEpisodesScan`): that command is for a release their own download
client tracking already knows about, and pointed at a folder with no such history it does nothing —
verified against a real Radarr and Sonarr while building this, mounted and reachable included.
Moving the file into the title's own folder and asking for a rescan scoped to it is the combination
that actually works, and it needs no extra folder, no extra mount, and no naming scheme — Radarr and
Sonarr were each checked against a file carrying an unrelated name and found it regardless, as long
as it sits in the right folder (Sonarr still needs a season/episode number *somewhere* in the name,
to tell episodes apart within one series — a film's whole folder is unambiguously one title).

**This requires the panel's own container to see Radarr's/Sonarr's library folders, mounted
read-write, at the exact same path inside the container that Radarr/Sonarr themselves use.** The
destination is whatever `path` Radarr/Sonarr report over their own API, used as-is — nothing about
it is translated or configured on this panel. Mount a *different* folder at that same path in the
panel's container (a different share, a different bind mount, even one that happens to have the
same name) and the move still "succeeds" from the panel's point of view: it writes the file
somewhere, just not where Radarr/Sonarr — or Windows Explorer, or anything else looking at the real
library — will ever see it. Give the panel's container the same volume(s) Radarr/Sonarr already use
for their libraries, mounted at the same container path those use, the same way this panel's own
library folder is already mounted for Jellyfin.

Right after that rescan, the panel also asks Radarr/Sonarr to rename the file
(`RenameMovie`/`RenameSeries`) — their own command, their own naming settings. Verified against a
real Sonarr: dropped straight into the series' root folder, a file sits there as a flat file until
this runs; `RenameSeries` is what creates the season subfolder (`Stagione {season}` or whatever
**Impostazioni Media → Rinomina** has configured there) and moves the file into it, and renames it
too as long as "Rinomina episodi"/"Rinomina film" is switched on in Radarr/Sonarr itself — switched
off, the command still reorganises the folder and just leaves the filename alone, since that is what
the toggle being off means. Nothing about this is configured on the panel's side: whatever naming
scheme Radarr/Sonarr already use for everything else is what a panel-delivered file gets too.

Owning a request queued with accounts enabled is still a gap worth knowing: a sync match placed
through the normal request queue needs an owning user (`arr_managed_by_user_id` in `data.json`) —
there is no UI for it yet, so without one the sync leaves every match in the review list rather than
guessing who it belongs to. The move-and-rescan handoff itself has no such limit — it runs the same
way whether accounts are on or off.

---

## Naming

**Impostazioni → Nomi** sets how files and folders are named, per type. Each field is left blank
when it matches the default, and shows that default as its placeholder — so an untouched field
reads as "nothing changed here" rather than as a value somebody chose. Saving a blank field keeps
the default.

The defaults follow the structure [Jellyfin's own documentation](https://jellyfin.org/docs/general/server/media/movies/)
recommends, which is the layout below. Change them only if your library already follows a different
convention: Jellyfin recognises this one with no configuration at all.

Placeholders: `{title}`, `{year}`, `{season}`, `{season2}`, `{episode}`, `{episode2}` — the `2`
variants are zero-padded. Anything in square brackets appears only if the placeholders inside it
have a value, so `[ ({year})]` disappears entirely for a title with no year. Each field previews
itself as you type.

Changing a rule does not rename what is already there. Existing files keep being recognised, so
nothing is downloaded twice; they simply keep their old names until you rename them in the file
manager.

---

## Output structure

```
videos/
├── Movie (2020)/
│   ├── Movie (2020).mkv
│   └── Movie (2020).en.vtt
└── Series (2019)/
    └── Season 01/
        ├── Series S01E01.mkv
        └── Series S01E02.mkv
```

The container is yours to pick, in **Settings -> Download -> Output format**: `.mkv` (the default,
which holds any combination of tracks) or `.mp4`, for devices that cannot play Matroska — plenty of
older smart TVs, Chromecast and consoles. Nothing is re-encoded either way, so the choice costs no
download time: the source is already H.264 with AAC audio, which both containers take as they are.

Subtitles follow a second setting: muxed into the file, or written beside it as
`{name}.{lang}.vtt`, the layout Jellyfin expects. Inside an MP4 they have to be converted to
`mov_text`, which drops colours, bold and positioning — keeping them as separate files avoids that
entirely.

Changing either setting leaves what is already downloaded alone. Both extensions keep counting as
present, so nothing is re-downloaded because of it.

---

## License

MIT

---

## Disclaimer

This project is published for **informational and educational purposes only**. It exists to
demonstrate how HLS streams are parsed, how AES-CBC segment decryption works, how a download queue
and a permission model are built, and how the result is organised into a Jellyfin library.

It **hosts, stores and distributes nothing**. It contains no content, no catalogue and no index. It
is a client: it talks to third-party websites that it neither operates nor controls, and it has no
affiliation, sponsorship or endorsement from any of them — StreamingCommunity, AnimeUnity and
Jellyfin included. Every trademark belongs to its owner. Whether those sites are lawful to use, and
whether they remain reachable at all, is entirely outside this project's control.

Using it is your decision and your responsibility. You are the one who has to comply with the
copyright law of your country and with the terms of service of any site you point it at, and you
should only download content you hold the rights to or are otherwise entitled to access. Neither
the authors nor the contributors take any responsibility for how the software is used, nor for any
damage or legal consequence that follows from using it.

The software is provided "as is", without warranty of any kind, as set out in the
[LICENSE](LICENSE).

If you represent a rights holder and believe something here is a problem, please open an issue and
it will be addressed.
