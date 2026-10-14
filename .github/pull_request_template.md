<!-- Title: <type>(<scope>): <summary>   e.g. feat(api): add configurable layout levels -->

## What

<!-- What changes, in a few sentences. Link the issue or meeting note if there is one. -->

## Why

<!-- The problem this solves, and anything you considered and rejected. -->

## How to test

<!-- Steps a reviewer can follow. Say which mode: `make up` or `make demo`. -->

1.
2.

## Checklist

- [ ] Base branch is `dev`, or `stage`/`main` for a promotion or hotfix (CONTRIBUTING.md §6, §8)
- [ ] Branch is named `<type>/<initials>/<short-name>` and rebased on its base branch
- [ ] `make up` comes up cleanly
- [ ] Checks for what I touched pass (see CONTRIBUTING.md §4)
- [ ] UI change: screenshot attached and checked in the browser
- [ ] Schema change: noted below; reviewers need `make fresh`
- [ ] `contracts/` change: all four teammates requested as reviewers
- [ ] Docs updated (READMEs, COMMANDS.md, comments)
- [ ] No secrets, `.env` values or large files committed

## Notes for reviewers

<!-- Schema changes, follow-ups, anything you want a second opinion on. -->
