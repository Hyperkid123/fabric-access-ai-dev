# Jira project planner

The planner uses GPT-6.1 Terra to write a technical plan for one unassigned
Jira ticket labeled `dev-bot-planner` per scheduled cycle. It has its own
OpenCode persona, preflight, and planning skill. The existing coordinator
deployment hosts both Jira workflows.

## Jira setup

1. Describe the project goal on the Epic. Label the Epic with one or more
   repository keys, for example `repo:insights-rbac` and `repo:rbac-config`.
   Keys come from the [review repository list](../../rbac-config-review/agent/project-repos.json).
2. Put the planning request in the Epic itself or in a Jira ticket under
   the Epic. Leave the request unassigned and set `dev-bot-planner` on it.
   If the request stands alone, add the documentation line and repo labels
   directly to that ticket.
3. Wait for the scheduled scan. The planner checks the ticket and Epic,
   builds local context, then posts a technical plan on the labeled request.
   The plan includes phase goals, small or medium work items, dependencies,
   acceptance criteria, and how to verify each item. It proposes work in
   the comment. The Jira ticket stays unassigned.

The preflight skips a request after a plan has been published for its current
inputs. Edit the ticket or Epic description, summary, or repo labels to request
a revised plan. Changes to the docs base or default glob also trigger a new
plan. Changes to source files alone do not trigger a cycle. If a missing source
becomes available at the same path, update the request description to ask the
planner to reconsider it.

The Jira key identifies the ticket to read and comment on. The planner
generates a request ID from the inputs above to identify a revision. Users
only need to label the ticket.

## Project documentation

Add a line to the Epic description, with an optional directory filter on a
separate line:

```text
Project documentation: 'project_2'
Project documentation filter: 'design/*.md'
```

`project_2` is a path relative to `BOT_PLANNER_DOCS_BASE_URL`. Use a directory
or a direct file path such as `project_2/architecture.md`. The planner reads
directories recursively. A filter matches paths relative to the requested
directory. For example, `*.md` includes Markdown files at any depth and
`design/*.md` selects Markdown files beneath `design/`. A direct file
only needs to be valid UTF-8 text.

Supported base URL shapes:

| Location | Example |
|---|---|
| GitHub repository at its default branch | `https://github.com/example/team-docs` |
| GitHub directory at a branch, tag, or commit | `https://github.com/example/team-docs/tree/main/projects` |
| GitLab repository at its default branch | `https://gitlab.example.com/team/subgroup/team-docs` |
| GitLab directory at a branch, tag, or commit | `https://gitlab.example.com/team/subgroup/team-docs/-/tree/main/projects` |

Use a commit SHA for refs that contain slashes. The runner's Git credentials
must have read access. A directory contributes up to 20 text files, with
128 KiB per file and 512 KiB total. Missing or unreadable paths, empty filter
matches, and skipped files are reported in the manifest output and under
**Context gaps** in the Jira comment. The planner can produce a provisional
plan from the other available sources.

The repository labels select read-only snapshots and supporting documentation
from [review-context.yaml](../../rbac-config-review/agent/review-context.yaml).
The planner uses the selected repositories' default branches, the related
repositories, and the Markdown files configured for those selected repositories.
Links in the context point to the fetched commits.

## Deployment

Set the planner parameters on the existing
`fabric-access-ai-coordinator-deploy.yml` target in app-interface. The target
continues to use the coordinator profile at `instance/rbac-project-coordinator`.

| Parameter | Value |
|---|---|
| `BOT_PLANNER_DOCS_BASE_URL` | Trusted HTTPS repository or directory URL |
| `BOT_PLANNER_DOCS_GLOB` | Default directory filter, `*.md` |

The Jira connector needs permission to search and read requests and comment on
them. When planner preflight selects work, the coordinator runner delegates
the technical plan to the Terra planner persona. The runner rechecks planner
eligibility and input freshness before posting.
