# Your own Dana: upstream + your site

Run Dana as upstream ships it, the published image `ghcr.io/ronganam/dana:<version>`, and keep everything that is yours outside Dana's code: your settings in one folder beside the checkout, the **site**, and your own addons installed on your Dana like anyone's. Then updating is a new release, your changes never conflict with Dana's, and the code you run is the code everyone else runs and tests. Dana's own maintainer runs production exactly this way.

```
dana/                     ← upstream, unchanged: git pull to update
  server/  android/  extensions/
  site.example/           ← the template for your site
  site/  → ~/my-dana-site ← yours (gitignored here; a folder, or a link to your own private repository)
    deploy.env            how a release rolls out (GitOps values or a compose folder)
    android.env           your server URL (for publishing apps) and devices
    tv-defaults.json      your TV's first-run home screen
    google-services.json  your Firebase project (optional)
    AGENTS.md             notes about this deployment, for you and coding agents
```

`DANA_SITE=/path/to/site` uses a site somewhere else. What each file holds: [`site.example/README.md`](../site.example/README.md). The site holds settings, no code.

## What goes where

| You want to… | Put it in | Not in |
|---|---|---|
| Set keys, accounts, models, the timezone | `server/.env` or your secret store ([`server/docs/configuration.md`](../server/docs/configuration.md)) | the site (it may live in a repository) or the code |
| Connect Home Assistant, Google, WhatsApp, banks… | each user's addon connections: the web Addons page | env or code |
| Describe yourself to Dana (name, pronoun, context) | `OWNER_*` in `.env` for the first user, then the web profile | prompts in the code |
| Add a capability only you need | an addon in its own folder or (private) git repository, installed on your Dana from the folder (`bin/dana addons install ./my_addon --server https://your-dana`) or the git URL ([`server/docs/writing-an-addon.md`](../server/docs/writing-an-addon.md)) | `server/addons/`, the site, a custom image |
| Add a capability others could use | the same addon published to the catalog (`bin/dana addons publish`), or an official addon in `server/addons/` sent as a pull request | a fork |
| Fix or change how Dana itself works | a pull request upstream | a private patch |
| Deploy somewhere | the published image: `compose.yaml` at the repository root, or `site/deploy.env` + `server/bin/deploy <version>` ([`server/docs/operations.md`](../server/docs/operations.md), "Deploying") | a custom image, edits to the scripts |
| Point the apps at your server | pair each app from the web Devices page (`server/docs/devices.md`) | the Kotlin code or `android.env` |

When a change seems to need an edit to Dana's code just for your setup, the missing piece is usually a setting or an extension point. Ask for it in an issue, or add it in a pull request, instead of patching your copy.

## Start

```bash
git clone https://github.com/ronganam/dana.git && cd dana
cp -r site.example site              # or: ln -s ~/my-dana-site site
cp server/.env.example server/.env   # keys; README.md "Run the server"
git config core.hooksPath .githooks  # pushing master runs the tests
```

To keep the site under version control, make it its own private repository and link it as `site`. It holds no secrets, only addresses and serials. Your own addons live in their own folders or repositories, and their install records in Dana's data dir (`bin/dana addons list` says where each came from).

## Stay current

```bash
git pull                              # upstream changes; your site is untouched
server/bin/deploy <version> --wait    # roll out that published release
server/bin/dana addons test ~/my_addon  # your own addons' tests against the new Dana
```

Database migrations run by themselves at start, after a snapshot ([`server/docs/operations.md`](../server/docs/operations.md), "Database schema"). Installed addons use the same framework as the official ones (`docs/addons.md`), so a framework change that affects them is called out in the commit and in `server/docs/writing-an-addon.md`. They live in the data volume and stay installed across releases.

A release is a version: `/api/health` shows the one running.

## Working on Dana itself

Fork to send pull requests. Production runs releases, so a change reaches it once it is merged upstream and released; to try it before that, run your branch locally (`server/bin/local-env.sh`, [`server/docs/local-testing.md`](../server/docs/local-testing.md)). Nothing lives only in your copy.
