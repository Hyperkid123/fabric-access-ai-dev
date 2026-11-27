# Jira project planner

The planner uses GPT-6.1 Terra to write a technical plan when requested on a
coordinator control ticket. The existing coordinator scan routes the request
to the planner persona and skill.

## Jira setup

1. Describe the project goal on the control ticket's direct parent, which can
   be an Epic or another supported parent issue. Label the parent with one or
   more repository keys, for example `repo:insights-rbac` and `repo:rbac-config`.
   Keys come from the [review repository list](../../rbac-config-review/agent/project-repos.json).
2. Post `PLANNER: /coord plan` on the existing coordinator control ticket.
   Add optional focus after the command on the same first line, for example
   `PLANNER: /coord plan Focus on rollout and rollback`.
3. Wait for the scheduled scan. The planner reads the parent description
   and referenced context, then posts a technical plan on the control ticket.
   The plan includes phase goals, small or medium work items, dependencies,
   acceptance criteria, how to verify each item, and machine-readable phase
   and repository assignments for current same-Team implementation tickets.

The planner answers each command comment once. Update the parent brief or
source documentation as needed, then post a new `PLANNER: /coord plan` comment
to request a revised technical plan. The coordinator reads the planner's
assignments and uses them to propose and apply Jira labels; it does not create
or refresh the technical plan. Work items without an existing Jira ticket are
given `P-<number>` proposal IDs. The coordinator can create all proposed
tickets, proposals for one phase, or selected proposal IDs after an explicit
creation request. Coordinator requests start with `COORDINATOR:` on the first
line.

## Creating Jira tickets from the plan

The coordinator lists each proposed ticket on the control ticket with its
summary, scope, phase, repository, dependencies, and acceptance criteria. Its
`Possible next steps` footer offers these requests:

```text
COORDINATOR: /coord create plan
COORDINATOR: /coord create phase:01
COORDINATOR: /coord create P-1,P-3
```

`plan` creates all proposals for work without an existing Jira key, `phase:NN`
creates the proposals for one phase, and a comma-separated list creates only
those IDs. The action verifies the current planner snapshot and control
ticket scope, then sets the phase and repository labels. It does not add the
implementation bot label. After ticket
creation, request `PLANNER: /coord plan` so the planner can assign the real
Jira keys; the coordinator then publishes a fresh label proposal for approval
and handoff. Planner comments also show these creation options as next steps.

## Project documentation

Add one or more `Project documentation:` lines to the parent ticket's
description. When the parent has no `repo:` labels, put these declarations on
the coordinator control ticket; the planner then uses the project documents
without loading repository review context. Each line can name a relative Git
path, a GitHub/GitLab repository, directory, or file URL, a Jira issue key, or
a Jira issue URL. A Jira Epic works the same way as any Jira issue. For
example:

```text
Project documentation: 'project_2'
Project documentation filter: 'design/*.md'
Project documentation: 'https://github.com/example/team-docs/tree/main/architecture'
Project documentation: 'https://github.com/example/team-docs/blob/main/architecture/overview.md'
Project documentation: 'https://gitlab.cee.redhat.com/group/team-docs/-/blob/main/design/overview.md'
Project documentation: 'RHCLOUD-12345'
Project documentation: 'https://jira.example.com/browse/RHCLOUD-67890'
```

The planner reads the Jira sources using its configured Jira connector and
includes each referenced issue's summary and description. Jira references
work without a Git base URL. Put the key or link on the same line as the
declaration. The planner reports missing or empty Jira sources under
**Context gaps**.

`project_2` is a path relative to `BOT_PLANNER_DOCS_BASE_URL`. Full Git URLs
can point to repositories, directories, or individual files, and multiple Git
and Jira references are supported. Put an optional filter in the same ticket
description as the declarations. The planner reads directories recursively.
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

The parent repository labels select read-only snapshots and supporting documentation
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
| `BOT_PLANNER_DOCS_BASE_URL` | HTTPS repository or directory used to resolve relative Git paths |
| `BOT_PLANNER_DOCS_GLOB` | Default directory filter, `*.md` |

If no repo labels and no project documentation can be loaded, the planner
posts a context-gap comment and does not draft a technical plan. Add project
documentation to the control ticket and retry.

The Jira connector needs permission to read the parent and comment on the
control ticket. The existing coordinator preflight checks control ticket
comments. The runner rechecks the command and parent inputs before posting.
