# Contributing

How we take a change from idea to `main`. The rules are short so that everyone
actually follows them; if one of them gets in the way, raise it at the weekly
meeting rather than working around it.

**At a glance**

1. Branch from an up-to-date **`dev`**, named `<type>/<initials>/<short-name>`.
2. Commit in small steps with `<type>(<scope>): <summary>` messages.
3. Before opening a PR: rebase on `dev`, run the checks, run the system.
4. Open a PR **into `dev`**. One teammate approves; four approve a change to `contracts/`.
5. Squash-merge, then delete the branch.
6. Each week, `dev` is promoted to `stage` for integration testing, and a
   verified `stage` is promoted to `main`.

---

## 1. The three long-lived branches

```
feature/rc/…  ─┐
fix/hb/…      ─┼──▶  dev  ──▶  stage  ──▶  main
feature/pg/…  ─┘     │           │           │
                  integration  release    what we
                  of finished  candidate  demo and
                  work                    submit
```

| Branch | What it holds | Who merges into it | Must always |
|---|---|---|---|
| `dev` | Every finished feature and fix, as soon as it is reviewed | Anyone, via a reviewed PR from a work branch | Build, and come up with `make up` |
| `stage` | A snapshot of `dev` under integration testing: the release candidate | The person running the weekly integration, via a `dev → stage` PR | Pass the full integration check (§7) |
| `main` | Verified releases only. This is what we demo, present and submit. | Only via a `stage → main` PR, approved by the team | Work. No exceptions. |

**Rules for all three:**
- **Nobody pushes directly** to `dev`, `stage` or `main`. Every change arrives
  through a pull request.
- **Work branches are cut from `dev`**, never from `stage` or `main`. The only
  exception is a hotfix (§8).
- **Changes flow one way:** work branch → `dev` → `stage` → `main`. Fixes
  found on `stage` are made on a work branch, merged into `dev`, and promoted
  again. Never commit to `stage` directly.

---

## 2. Work branches

### Naming

```
<type>/<initials>/<short-name>
```

| Part | Rule | Examples |
|---|---|---|
| `type` | One of the types below | `feature`, `fix` |
| `initials` | Your initials, lower case. Use the same ones every time. | `rc`, `hb`, `pg`, `am` |
| `short-name` | 2–5 words, lower case, words joined by hyphens, and no ticket numbers or dates | `layout-levels`, `lcd-driver` |

**Our initials:**

| Initials | Name | Area |
|---|---|---|
| `hb` | Het Bhalala | Edge gateway, MSP432, labels (L1–L2) |
| `rc` | Rodrigo Chen | Dashboard API and web (L5) |
| `pg` | Parth Gala | Pricing agent, simulator (L4) |
| `am` | Aruna Samhitha Mutyala | Forecaster (L4) |

**Types:**

| Type | Use it for | Example |
|---|---|---|
| `feature` | New behaviour a user or another layer can see | `feature/rc/layout-levels` |
| `fix` | A bug in something that already exists | `fix/hb/lcd-line2-truncation` |
| `hotfix` | An urgent fix to `main` that cannot wait for the next release (§8) | `hotfix/rc/signin-crash` |
| `refactor` | Restructuring with no change in behaviour | `refactor/pg/agent-reward-module` |
| `docs` | Documentation, workbook and meeting notes only | `docs/am/forecaster-readme` |
| `test` | Tests or test tooling only | `test/rc/smoke-script` |
| `chore` | Build, dependencies, CI, repo housekeeping | `chore/rc/compose-healthchecks` |
| `contract` | Any change to `contracts/` (see §5) | `contract/pg/proposal-confidence` |
| `spike` | A throwaway experiment that will **not** be merged | `spike/am/lstm-vs-prophet` |

**Good:** `feature/hb/eink-partial-refresh`, `fix/rc/invite-link-expiry`

**Not:** `feature/dashboard_01`, `rodrigo-changes`, `feature/RC/NewStuff`, `fix/rc/fix-bug`

### Lifetime

- **Keep a branch short-lived.** Aim to merge into `dev` within a few days. A
  branch older than a week is usually two branches.
- **One branch, one change.** A feature that touches the API, the web app and
  the schema is still one branch. Two unrelated fixes are two branches.
- **Delete it after merging.** GitHub offers this on the PR page; take it.

---

## 3. Starting a change

```bash
git switch dev
git pull --ff-only
git switch -c feature/rc/layout-levels
make up                         # confirm dev works before you change anything
```

If `dev` does not come up cleanly, fix that first (on its own `fix/` branch) or
tell the team. Never start a feature on top of a broken base.

---

## 4. Commits

### Message format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary>

<optional body: why, not what>
```

- **`type`:** the same list as branch types, except that `feature` is written
  `feat` and `hotfix` is written `fix`.
- **`scope`:** the area you touched:
  - `api`, `web`, `agent`, `forecaster`, `simulator`;
  - `gateway`, `msp432`, `eink`;
  - `infra`, `contracts`, `tools`, `docs`.
- **`summary`:** imperative mood, lower case, no full stop, at most 72 characters.
  Write "add …", not "added …" or "adds …".

```
feat(api): add configurable layout levels
fix(web): keep level name fields the same width
chore(infra): move bootstrap account to the demo overlay
docs(contracts): explain the approval enum
```

The body is optional, but write one whenever the reason is not obvious from the
diff. Explain why the change was needed and what you considered.

### Habits

- **Commit small and often.** Each commit should build. When you squash-merge
  into `dev`, the PR title becomes the final message.
- **Never commit secrets.**
  - `.env` is gitignored. Credentials belong there, never in code, seed files
    or docs.
  - If you commit one by accident, tell the team: removing it in a new commit
    is not enough.
- **Don't commit generated or bulky files.** That means `node_modules`, virtual
  environments, model checkpoints and datasets over a few MB. Ask before adding
  a large file.
- **`03-seed.sql` is generated.** Edit `tools/gen_seed.py`, then run `make seed`.

---

## 5. Pull requests into `dev`

### Before opening one

Bring your branch up to date:

```bash
git fetch origin
git rebase origin/dev           # resolve conflicts, then: git rebase --continue
git push --force-with-lease     # only ever force-push your own branch
```

Then run the checks that apply:

| You changed | Run |
|---|---|
| Anything | `make up` (the stack must come up cleanly with one command) |
| `services/dashboard-web/` | `cd services/dashboard-web && npm run typecheck` |
| `contracts/` | `make validate` |
| `infra/postgres/` | `make fresh`, plus `make fresh-demo` if the seed is affected (see the note below) |
| Behaviour across layers | `make demo`, then exercise the flow in the console |

> **Schema changes.** Postgres applies `infra/postgres/*.sql` only when a
> database volume is first created. If your PR changes the schema, say so in the
> PR description. Reviewers and teammates will need `make fresh`, which deletes
> their local data. If the old schema would break, add the new table to the
> startup check in `dashboard-api`'s `_check_schema`, so that the API stops with
> a clear message instead.

Finally, read your own diff on GitHub before asking for review:
- **Leftovers:** debug output, commented-out code and unrelated formatting
  changes are easiest to catch there.
- **UI changes:** check them in the browser, not just with the type checker.
- **Docs:** update the ones your change makes wrong (READMEs, `COMMANDS.md`,
  comments next to the code).

### Opening one

- **Base branch: `dev`.** GitHub may default to `main`, so check before you
  click Create.
- **Title.** Write it as the final commit message, in the same
  `<type>(<scope>): <summary>` form.
- **Description.** Fill in the template, which loads automatically. Cover what
  changed, why, how you tested it, and anything a reviewer must do, such as
  `make fresh`.
- **Screenshots.** Add one for any visible UI change, and before/after where it
  helps.
- **Drafts.** Open a **Draft** PR early if you want feedback on direction.
- **Size.** Keep a PR small enough to review in about 20 minutes, which is
  roughly 400 changed lines excluding generated files.

### Reviews

| The PR touches | Approvals needed |
|---|---|
| Anything outside `contracts/` | **1** teammate, ideally the owner of the area |
| `contracts/` (MQTT payload schemas) | **All 4** |

**Contracts are the interface between our layers.** A change there can break
someone else's code without touching it, which is why it needs everyone. Put a
contract change in its own `contract/` PR before the code that depends on it.

**Reviewing:**
- Respond within one working day, even if it is only "looking tomorrow".
- Review for correctness first: does it do what it says, and what happens when
  the broker, the database or the label is down?
- Then review for clarity. Style comments come last.
- Mark a comment that blocks the merge clearly, for example with **Blocking:**.
  Everything else is a suggestion the author may decline.
- Approve once the blocking points are resolved. Don't hold a PR for nits.

**Responding to review:**
- Reply to every comment, either with a fix or with a reason.
- Push fixes as new commits during review so reviewers can see what changed.

### Merging

- **Squash and merge.** Each PR becomes one commit on `dev`, titled with the PR
  title.
- **The author merges** after approval.
- **After merging:**
  - delete the branch;
  - pull `dev` and run `make up` to confirm it still comes up.

  If it doesn't, fixing it takes priority over anything else.

---

## 6. Promotions: `dev → stage → main`

Promotions are pull requests between the long-lived branches. **Use a merge
commit, not squash or rebase**, so that the three branches keep a shared history
and the next promotion contains only what is new.

### `dev → stage` (weekly, before the team meeting)

1. Open a PR from `dev` into `stage`, titled `release: <yyyy-mm-dd>`. In the
   description, list the merged PRs it brings in.
2. Merge it with **Create a merge commit**. It needs one approval.
3. Run the integration check (§7) on `stage`.
4. **If something is broken:**
   - fix it on a `fix/` branch cut from `dev`;
   - merge the fix into `dev`, and promote again.
   - Never patch `stage` directly.

### `stage → main` (when `stage` has passed the integration check)

1. Open a PR from `stage` into `main`, titled `release: <yyyy-mm-dd>`.
2. **All four** approve. This is what we present and submit, so everyone signs
   off on it.
3. Merge with **Create a merge commit**.
4. Tag the release:

   ```bash
   git switch main && git pull --ff-only
   git tag -a v0.<n>.0 -m "release: <yyyy-mm-dd>"
   git push origin v0.<n>.0
   ```

   Tags let us return to exactly what was demoed on a given date.

---

## 7. Weekly integration check (on `stage`)

One person, rotating each week, runs this:

1. Pull `stage`, then `make fresh` and `make up`.
2. Go through the first-run flow:
   - create the Super admin;
   - set up the layout levels;
   - add a location and a product;
   - link the product to the location.
3. `make fresh-demo`, then `make demo`. Check that suggestions arrive and can be
   approved, and that labels report.
4. `make smoke`, once it exists.
5. Post the result in the team chat. If anything is red, it is the first item on
   the meeting agenda.

Meeting notes go in [`docs/meetings/`](docs/meetings/). They are workbook
material.

---

## 8. Hotfixes

Use one only when `main` is broken and the fix cannot wait for the next weekly
release, for example the day before a demo.

1. Branch from **`main`**: `hotfix/<initials>/<short-name>`.
2. PR into `main`. It needs one approval. Merge with a merge commit, then tag a
   patch release, e.g. `v0.3.1`.
3. **Immediately** merge `main` back into `stage` and `dev` with two PRs, so the
   fix is not lost at the next promotion.

---

## 9. GitHub settings (one-time setup)

So that these rules are enforced rather than remembered:

- **Branches:** create `dev` and `stage` from `main`, and make **`dev` the default
  branch**. PRs then target it automatically.
- **Branch protection on `dev`, `stage` and `main`:**
  - require a pull request before merging, with no direct pushes;
  - require approvals: 1 on `dev` and `stage`, 4 on `main`;
  - block force pushes and deletion.
- **Merge buttons:** allow both **Squash** (for work → `dev`) and **Merge commit**
  (for promotions), and disable **Rebase**.
- **Automatically delete head branches** after merge. The long-lived branches
  are protected, so this only removes work branches.

---

## Quick reference

```bash
# start a change
git switch dev && git pull --ff-only
git switch -c feature/<initials>/<short-name>

# work
git add -p
git commit -m "feat(<scope>): <summary>"

# before the PR (base branch: dev)
git fetch origin && git rebase origin/dev
make up                                   # plus the checks in §5
git push -u origin HEAD                   # first push
git push --force-with-lease               # after a rebase

# after merge
git switch dev && git pull --ff-only
git branch -d feature/<initials>/<short-name>
```

| I want to… | Branch from | PR into | Merge with |
|---|---|---|---|
| Build a feature or fix | `dev` | `dev` | Squash |
| Promote for integration testing | `dev` | `stage` | Merge commit |
| Release | `stage` | `main` | Merge commit, then tag |
| Fix `main` urgently | `main` | `main`, then back into `stage` and `dev` | Merge commit |
