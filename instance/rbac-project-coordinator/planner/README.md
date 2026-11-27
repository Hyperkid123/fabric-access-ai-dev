# Jira project planner

The planner uses GPT-6.1 Terra to write a technical plan when requested on a
coordinator control ticket. The existing coordinator scan routes the request
to the planner persona and skill.

## Jira setup

1. Describe the project goal on the control ticket's direct parent, which can
   be an Epic or another supported parent issue. Label the parent with one or more
   repository keys, for example `repo:insights-rbac` and `repo:rbac-config`.
   Keys come from the [review repository list](../../rbac-config-review/agent/project-repos.json).
2. Post `/coord planner` on the existing coordinator control ticket.
   Add optional focus after the command, for example
   `/coord planner Focus on rollout and rollback`.
3. Wait for the scheduled scan. The planner reads the parent description
   and referenced context, then posts a technical plan on the control ticket.
   The plan includes phase goals, small or medium work items, dependencies,
   acceptance criteria, and how to verify each item.

The planner answers each command comment once. Update the parent brief or
source documentation as needed, then post a new `/coord planner` comment
to request a revised technical plan. Use `/coord plan` to refresh the
coordinator's plan for existing implementation tickets.

## Project documentation

Add `Project documentation:` to the parent ticket's description. Each
line can name a Git path, a Jira issue key, or a Jira issue URL. A Jira Epic
works the same way as any Jira issue. For example:

```text
Project documentation: 'project_2'
Project documentation filter: 'design/*.md'
Project documentation: 'RHCLOUD-12345'
Project documentation: 'https://jira.example.com/browse/RHCLOUD-67890'
```

The planner reads the Jira sources using its configured Jira connector and
includes each referenced issue's summary and description. Jira references
work without a Git base URL. Put the key or link on the same line as the
declaration. The planner reports missing or empty Jira sources under
**Context gaps**.

`project_2` is a path relative to `BOT_PLANNER_DOCS_BASE_URL`. Use a directory
or a direct file path such as `project_2/architecture.md`. The planner accepts
one Git path and multiple Jira references. Put an optional filter in the
parent description. The planner reads directories recursively.
A filter matches paths relative to the requested
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

The Jira connector needs permission to read the parent and comment on the
control ticket. The existing coordinator preflight checks control ticket
comments. The runner rechecks the command and parent inputs before posting.
