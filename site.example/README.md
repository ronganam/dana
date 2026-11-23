# Your Dana site

A **site** is everything that makes a Dana deployment yours, and nothing else. Dana's code stays as upstream ships it.

Start yours from this folder:

```bash
cp -r site.example site               # site/ is gitignored
# or keep it in your own private repository and link it:
ln -s ~/my-dana-site site             # or point DANA_SITE at it
```

| File | Read by | What |
|---|---|---|
| `deploy.env` | `server/bin/deploy` | How a published release rolls out: your GitOps values file or compose folder, and how to run `dana` where the server runs (fill in `deploy.env.example` and rename it) |
| `android.env` | the Android build, `android/bin/deploy-*.sh` | Your server's URL (the apps' default), the TV's LAN DNS, your adb serials, how to get the publish token |
| `tv-defaults.json` | the Android TV build | The TV home screen's first-run apps, Home Assistant entities, cameras and remote buttons |
| `google-services.json` | the Android phone build | Your Firebase project, for direct push (optional) |
| `AGENTS.md` | you and coding agents | Notes about this deployment: where it runs, its devices, how to reach production |

The site holds settings only, no code. An addon only you need lives in its own folder or repository and is installed on your Dana like any other (`bin/dana addons install ./my_addon --server https://your-dana`; `server/docs/writing-an-addon.md`).

Secrets stay out of the site: the server's keys go in `server/.env` or your secret store, and the release keystore in `android/keystore.properties`.

How the parts fit, and how to stay on upstream without drifting: `docs/site.md` at the repository root.
