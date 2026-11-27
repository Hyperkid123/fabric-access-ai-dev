# Jira project planner

The planner uses GPT-6.1 Terra to write a technical plan for one unassigned
Jira ticket labeled `dev-bot-planner` per scheduled cycle. It has its own
OpenCode persona, preflight, and planning skill. Deploy it alongside the
coordinator using the same runner image and a separate profile.

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

Use `deploy/template.yaml` or `deploy/rbac-config-template.yaml` in the
deployment target. Set a distinct `BOT_NAME` and `BOT_INSTANCE_ID` and supply
the same Git and Jira connector access as the other Fabric Access profiles.

| Parameter | Value |
|---|---|
| `BOT_CONFIG_REPO` | The Fabric Access bot config repository |
| `BOT_CONFIG_PATH` | `instance/rbac-project-coordinator/planner` |
| `BOT_LABEL` | `dev-bot-planner` |
| `BOT_JIRA_PROJECT` | `RHCLOUD` or the target Jira project |
| `BOT_EXECUTION_ENGINE` | `coordinator`, the runner engine that hosts OpenCode |
| `REHOR_OPENCODE_DEPLOYMENT_CONFIG` | `/home/botuser/app/instance/rbac-project-coordinator/opencode-deployment.json`, which contains the Terra provider configuration |
| `BOT_PLANNER_DOCS_BASE_URL` | Trusted HTTPS repository or directory URL |
| `BOT_PLANNER_DOCS_GLOB` | Default directory filter, `*.md` |

The Jira connector needs permission to search and read requests and comment on
them. Use one active planner replica for a Jira project. The runner starts a
planner session only when preflight selects a request. The planner rechecks
eligibility and input freshness before posting.
