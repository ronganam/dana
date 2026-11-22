# Dana website

The project's one-page site: what Dana is for, a showreel of one day with it (scenes that advance on their own,
with chapters, pause, arrow keys, and no motion under reduced motion), how it differs from a chat assistant,
what it does, where it runs, and how to run and extend it.

Plain static files, no build step: `index.html`, `style.css` (the apps' "Ember" design:
`../server/docs/web-design.md`), `site.js`, `favicon.png`. Open `index.html` in a browser, or serve the folder
from any static host. `.github/workflows/pages.yml` publishes it to GitHub Pages on every push to master that
changes it, once the repository is public and Pages is set to deploy from GitHub Actions.

The people, places and amounts on the page are made up; keep it that way.
