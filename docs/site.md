# Your own Dana: upstream + your site

Run Dana as upstream ships it, and keep everything that is yours in one folder beside it, the **site**. Then updating is `git pull`, your changes never conflict with Dana's, and the code you run is the code everyone else runs and tests. Dana's own maintainer runs production exactly this way.

```
dana/                     ← upstream, unchanged: git pull to update
  server/  android/  extensions/
  site.example/           ← the template for your site
  site/  → ~/my-dana-site ← yours (gitignored here; a folder, or a link to your own private repository)
    deploy.env            where your images go and how they roll out
    plugins/<kind>/       your own integration kinds
    android.env           your server URL and devices, for the apps
    tv-defaults.json      your TV's first-run home screen
    google-services.json  your Firebase project (optional)
    AGENTS.md             notes about this deployment, for you and coding agents
```

`DANA_SITE=/path/to/site` uses a site somewhere else. What each file holds: [`site.example/README.md`](../site.example/README.md).

## What goes where

| You want to… | Put it in | Not in |
|---|---|---|
| Set keys, accounts, models, the timezone | `server/.env` or your secret store ([`server/docs/configuration.md`](../server/docs/configuration.md)) | the site (it may live in a repository) or the code |
| Connect Home Assistant, Google, WhatsApp, banks… | each user's integrations: the web Integrations page | env or code |
| Describe yourself to Dana (name, pronoun, context) | `OWNER_*` in `.env` for the first user, then the web profile | prompts in the code |
| Add a capability only you need | a plugin in `site/plugins/` ([`server/docs/plugins.md`](../server/docs/plugins.md)) | `server/integrations/` |
| Add a capability others could use | a kind in `server/integrations/`, sent as a pull request | a plugin you keep forever |
| Fix or change how Dana itself works | a pull request upstream | a private patch |
| Deploy somewhere | `site/deploy.env` + `server/bin/deploy`, or `server/compose.yaml` | edits to the scripts |
| Point the apps at your server | `site/android.env` (`SERVER_URL`) | the Kotlin code |

When a change seems to need an edit to Dana's code just for your setup, the missing piece is usually a setting or an extension point. Ask for it in an issue, or add it in a pull request, instead of patching your copy.

## Start

```bash
git clone https://github.com/ronganam/dana.git && cd dana
cp -r site.example site              # or: ln -s ~/my-dana-site site
cp server/.env.example server/.env   # keys; README.md "Run the server"
git config core.hooksPath .githooks  # pushing master runs the tests (and deploys, with site/deploy.env)
```

To keep the site under version control, make it its own private repository and link it as `site`. It holds no secrets, only addresses, serials and your plugins.

## Stay current

```bash
git pull                 # upstream changes; your site is untouched
server/bin/test          # optional: the suite, with your site's plugins left out
server/bin/deploy --wait # or push master with site/deploy.env: tested, then deployed
```

Database migrations run by themselves at start, after a snapshot ([`server/docs/operations.md`](../server/docs/operations.md), "Database schema"). Plugins use the same framework as the built-in kinds, so a framework change that affects them is called out in the commit and in `server/docs/plugins.md`. Run your plugin's own test, if you wrote one, after a pull.

A release name says exactly what runs: `<VERSION>-<commit>`, plus `-p<hash>` when the image carries site plugins. `/api/health` shows it.

## Working on Dana itself

Fork to send pull requests, and run from upstream `master`. When you need a change now, put it on a branch, deploy that branch with `server/bin/deploy <commit>`, and open the pull request. Once it is merged, go back to `master`, so nothing lives only in your copy.
