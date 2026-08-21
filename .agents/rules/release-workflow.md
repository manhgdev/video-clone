# Release workflow rule

Every time you create a git tag and trigger GitHub Actions to build a release:

1. **Bump version first** — increment the patch version in both `package.json`
   and `build_app/VERSION` before creating the tag.
2. Commit the version bump with message `chore: bump version to X.Y.Z`.
3. Push the commit to `main`.
4. Create the annotated tag matching the new version: `git tag -a vX.Y.Z -m "..."`.
5. Push the tag: `git push origin vX.Y.Z`.
6. Only create tags / trigger Actions when the user explicitly asks.
