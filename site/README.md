# Website

This repository's website uses the Amplifier Smart Tools theme. The content of the page is
`site.yaml`, and its media is in `site/assets/`. The version and platforms on the page come
from the tool's manifest.

The canonical theme is maintained in `microsoft/amplifier-smart-tools/site/theme`. This
repository carries a copy in `site/theme/`, so a build does not depend on a remote theme.
Theme version: 0.3.0. Theme code is MIT licensed. The copy is excluded from this
repository's lint and type checks.

## Build and preview

From the repository root:

```sh
uv run --no-project --with-requirements site/requirements.txt python site/theme/build.py
python3 -m http.server 8000 --directory _site --bind 127.0.0.1
```

Open http://127.0.0.1:8000. The output goes to `_site/`, which Git ignores.

## Publish

The Website workflow builds each pull request that changes the site and does not deploy it.
A push to main that changes the site or the manifest builds the site and publishes it to
GitHub Pages. In **Settings > Pages**, select **GitHub Actions** as the source.

## Update the theme

From a checkout of `microsoft/amplifier-smart-tools`, run `site/sync_theme.py` with the path
of this repository. Review the diff and build again. It updates only `site/theme/`.
