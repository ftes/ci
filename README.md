# ci

Shared GitHub Actions steps and workflows for ftes's repos. Pin them by commit, like any action
(`ftes/ci/setup@<sha> # v1.2.3`); Renovate's `github>ftes/renovate-config` preset opens one PR
per release and merges it once the repo's checks pass, so each repo's CI tests each release.

| | |
|---|---|
| [`setup`](setup/action.yml) | Erlang (precompiled) and Elixir with mise, deps and `_build` from the cache, deps compiled |
| [`playwright-browser`](playwright-browser/action.yml) | the Playwright driver, a browser and its system packages, cached |
| [`kamal-deploy`](kamal-deploy/action.yml) | Ruby and Kamal with mise, SSH with pinned host keys, `kamal deploy` |
| [`elixir-image.yml`](.github/workflows/elixir-image.yml) | reusable: keeps the Dockerfile's hexpm/elixir image on the repo's toolchain |
| [`usage-rules-sync.yml`](.github/workflows/usage-rules-sync.yml) | reusable: commits `mix usage_rules.sync` to Renovate's PRs |

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

Triggers and job conditions can't be shared, so each repo's CI copies these lines. Any base branch
runs, so stacked PRs get CI; drafts, and PRs below the top of a GitHub stack (the top holds every
change below it), skip their jobs. A required check that aggregates jobs (`needs:` with
`if: always()`) puts the same condition on its step instead, so those PRs report it green.

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}
  cancel-in-progress: true

jobs:
  test:
    if: ${{ !github.event.pull_request.draft && (github.event.pull_request.stack == null || github.event.pull_request.stack.position == github.event.pull_request.stack.size) }}
```

An app that deploys main's head keeps its push runs in one group instead (`github.ref` for
`push` too, `cancel-in-progress` on PRs only), so pushes in between are dropped.

Each push to main is tagged as the next patch release (`tag.yml`); tag a minor or major by hand.
