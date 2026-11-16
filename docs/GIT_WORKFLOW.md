# Git workflow

Repository: https://github.com/Shushkata/Steam-Deck-Trigger-Calibration

`main` is the default stable branch. Work on a focused branch for later changes:

```sh
git switch main
git switch -c fix/short-description
# Make and check the intended change, then review the staged diff.
git add <reviewed-files>
git diff --cached
git commit -m "Describe the change"
# Push only when the change is ready to share.
git push -u origin fix/short-description
```

Merge reviewed changes into `main`. Do not rewrite published history or force-push
over another branch. Before the initial push, check remote branches again; if the
remote already has commits, fetch and reconcile them without overwriting history.

Never commit controller backups, state, logs, diagnostics, personal device readings
or credentials. `.gitignore` covers the known runtime names and exports at every
depth. Tests contain deliberately synthetic values, not a user's calibration.
Use `git status --short --ignored` and `git diff --cached` before committing. Ignore
rules do not remove previously tracked files or secrets from old commits.

Build outputs and install/source ZIPs are generated locally. Build and package
using the README instructions; `.gitignore` excludes those outputs from source
control. Keep all license files and third-party notices with redistributed builds.

Authentication is managed by Git/GitHub's credential helper or SSH; never embed a
token in `origin`, source files or documentation. Commit identity is configured
locally for this repository with a GitHub no-reply email.
