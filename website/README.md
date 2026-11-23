# Dana website

The project's one-page site: what Dana is for, a showreel of one day with it (scenes that advance on their own,
with chapters, pause, arrow keys, and no motion under reduced motion), how it differs from a chat assistant,
what it does, where it runs, how to run and extend it, and what it costs to run (one real household's
model spend: a monthly total and the split by purpose, no other amounts).

`make-an-addon.html` is a short page on building an addon with a coding agent (the scaffold, the loop, publishing),
and `llms.txt` points agents at the add-on guide (`server/cli/template/AGENTS.md.tmpl`) and reference
(`server/docs/addon-reference.md`) on GitHub.

Plain static files, no build step: `index.html`, `make-an-addon.html`, `llms.txt`, `style.css` (the apps' "Ember" design:
`../server/docs/web-design.md`), `site.js`, `favicon.png`. Open `index.html` in a browser, or serve the folder
from any static host. `.github/workflows/pages.yml` publishes it to GitHub Pages on every push to master that
changes it, once the repository is public and Pages is set to deploy from GitHub Actions.

The people, places and amounts on the page are made up, except the cost section; keep it that way. Every claim on
the page must be something the code does today: change the copy in the same commit as a feature it describes.
