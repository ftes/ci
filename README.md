# ci

Shared GitHub Actions steps and workflows for ftes's repos. Pin them by commit, like any action
(`ftes/ci/setup@<sha> # v1.2.3`); Renovate's `github>ftes/renovate-config` preset opens one PR
per release and merges it once the repo's checks pass, so each repo's CI tests each release.

| | |
|---|---|
| [`setup`](setup/action.yml) | Erlang (precompiled) and Elixir with mise, deps and `_build` from the cache, deps compiled; fails if `deps.get` changes `mix.lock` (`mix.lock.oldest` on the oldest lane) |
| [`playwright-browser`](playwright-browser/action.yml) | the Playwright driver, a browser and its system packages, cached |
| [`claude`](claude/action.yml) | claude-code-action with the shared model, tool rules and no attribution, a safe step summary |
| [`claude-tag`](claude-tag/action.yml) | for `@claude`: skips other repositories' and Renovate's PRs, lists maintainers' comments and the allowed `gh` commands |
| [`claude-review`](claude-review/action.yml) | a PR review's increment since the last one, maintainers' comments and the prompt |
| [`review-threads`](review-threads/action.yml) | a PR's open findings by reviewer bots, for Claude to address; [`answer`](review-threads/answer/action.yml) replies, reacts and resolves the fixed ones |
| [`kamal-deploy`](kamal-deploy/action.yml) | Ruby and Kamal with mise, SSH with pinned host keys, `kamal deploy` |
| [`elixir-image.yml`](.github/workflows/elixir-image.yml) | reusable: keeps the Dockerfile's hexpm/elixir image on the repo's toolchain |
| [`usage-rules-sync.yml`](.github/workflows/usage-rules-sync.yml) | reusable: commits `mix usage_rules.sync` to Renovate's PRs |
| [`release.exs`](release/release.exs) | a library's release, run locally from a checkout of main: `release/release.exs fluffy 0.9.0` |

Each file's header shows how to call it.

## Rules

- **Mechanics only.** No hostnames, IPs, secret names or anything else about one environment:
  those stay in the repo that uses them, and this repo stays public.
- **Steps, not jobs.** Triggers, services, matrices, permissions, secrets and the required check
  stay in each repo's workflow, where they can be read and audited. A reusable workflow is only
  for a job that is the same everywhere, and none touches OIDC: a reusable workflow changes the
  token's claims (Hex's workload identity, Claude's token exchange).
- **Flat.** An action here can't call another one at its own commit, so callers compose them.
- **Small.** A need of one repo is a step next to the shared one in that repo, not a new input.

## Pull request CI

Triggers and job conditions can't be shared as an action, so the CI of each repo that uses these
copies the lines below. Any base branch runs, so stacked PRs get CI; drafts, and PRs below the top
of a GitHub stack (`gh stack link`; a `--base` chain alone isn't one, and each of its PRs runs CI),
skip their jobs: the top holds every change below it. A required check that aggregates jobs
(`needs:` with `if: always()`) puts the condition on its step instead, so those PRs report it green.

```yaml
on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}
  cancel-in-progress: true

jobs:
  test:
    if: ${{ !github.event.pull_request.draft && (github.event.pull_request.stack == null || github.event.pull_request.stack.position == github.event.pull_request.stack.size) }}
```

So the top's CI vouches for the whole stack: merge it bottom-up (each merge's push to main runs CI
again), keep the top ready rather than a draft, and re-run CI on a PR that becomes the top when
the one above it closes, since no event starts a run for it.

An app that deploys main's head keeps its push runs in one group instead (`github.ref` for `push`
too, `cancel-in-progress` on PRs only), so pushes in between are dropped.

Each push to main that changes more than docs is tagged as the next patch release (`tag.yml`);
tag a minor or major by hand.
