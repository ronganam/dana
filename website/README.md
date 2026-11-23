# Dana website

The project's one-page site, in six sections the sticky nav follows (the current one is highlighted, a thin line
under the nav shows how far down you are):

- **A day**: the showreel of one day with Dana (scenes that advance on their own, with chapters, pause, arrow keys,
  and no motion under reduced motion).
- **How it works**: "Everything in. One thing out.", a live diagram (SVG + `site.js`, no libraries) of what Dana takes
  in (notifications, calls, the phone's signals, accounts, bank & cards, you), the steps on your server (Sort,
  Understand, Remember, Decide), what comes out and the model provider outside the server. The output side is the
  showreel's devices, small: a phone on its Now screen (the date, the serif lede, the day strip, rows with an app icon
  or the kind's dot, one muted line and the primary as a pill), a watch over its corner and a wall tablet with the
  shared list. Every 1.5 s a made-up event travels through it; most are kept quiet, and a counter shows read vs. told.
  What gets through shows up where it lands: a row slides into Now (newest on top, at most three, each tapped away
  after a while), a push drops in as a banner and then settles into the list as its row, the briefing replaces the
  watch's time, milk and eggs join the tablet's list; the lede follows the list ("All clear." to "3 things need
  you."). It pauses off screen and in a hidden tab, is static under reduced motion (three rows, the briefing, the
  list), stacks vertically on a phone (the devices under the server box), and is hidden from screen readers except a
  short text summary. Each event is something the code does: triage buckets and escalation
  (`server/addons/phone/notifications/triage.py`, `escalation.py`), call suggestions
  (`server/addons/phone/calls/suggest.py`), wake / car / place signals (`server/addons/phone/signals.py`,
  `server/core/wake.py`, the tasks addon's triggers), meeting cards with people briefs (`server/core/briefing.py`
  `check_meetings`, `server/addons/people/briefs.py`), facts about you (`server/addons/about/learner.py`), money
  categories and price changes (`server/addons/money/insights.py`, its Now cards in `hooks.py`), fired reminders as
  Now rows with "Done" (`server/addons/tasks/hooks.py`), the shared shopping list (`server/addons/shopping/`). The Now
  screen's look is the apps' (`server/static/app-now.js`, `android/DESIGN.md` "Now").
- **Why Dana**: how it differs from a chat assistant, and undo-first safety.
- **Everywhere**: phone, watch, wall tablet, TV, web, Telegram and desktops, in one row.
- **Make it yours** and **What it costs**, side by side: running it, add-ons (installed from the catalog, a git
  repository or a folder), a **"Let your coding agent do it"** box with the two one-line prompts (install, build an
  add-on) and a Copy button each (`copy.js`), the manual commands, and one real household's model spend (a monthly
  total and the split by purpose, no other amounts).

**Guides for coding agents**, plain Markdown served as is (the one-line prompts point at their URLs, e.g.
`https://ronganam.github.io/dana/install.md`):

- `install.md`: what to ask the person (where Dana runs, Docker, how it is reached, the model provider, who uses it,
  which apps), then the quick start, the setup link, HTTPS access, pairing the apps, Telegram, backups, checks; and the
  agent's rules (never print keys, never expose the server without auth, confirm destructive steps, stop on failure).
- `build-an-addon.md`: the interview (what Dana should do and two example requests, the service and its key, read or
  act, when, where it shows up, who, region), the design proposed back as the manifest, the checkout and scaffold
  (`bin/dana addons new NAME` → `./NAME`), the check / test / dev loop, `bin/dana addons install ./NAME --server URL
  --restart`, turning it on, trying the examples, publishing if wanted.
- `llms.txt`: the two guides and the docs indexes on GitHub.

The repository's Claude Code skills (`.claude/skills/dana-install`, `dana-addon`) and a new add-on's `AGENTS.md`
point at these guides; change the guides, not them. `make-an-addon.html` is a short page on building an add-on (the
scaffold, the loop, installing, publishing) with the add-on prompt.

Plain static files, no build step: `index.html`, `make-an-addon.html`, `install.md`, `build-an-addon.md`, `llms.txt`,
`copy.js`, `style.css` (the apps' "Ember" design:
`../server/docs/web-design.md`), `site.js`, `favicon.png`. Open `index.html` in a browser, or serve the folder
from any static host. `.github/workflows/pages.yml` publishes it to GitHub Pages on every push to master that
changes it, once the repository is public and Pages is set to deploy from GitHub Actions.

The people, places and amounts on the page are made up, except the cost section; keep it that way. Every claim on
the page and in the guides must be something the code does today: change the copy in the same commit as a feature it describes.
