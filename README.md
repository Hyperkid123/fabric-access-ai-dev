# fabric-access-ai-dev

Custom bot runner instance for the Fabric Access team, built on [dev-bot](https://github.com/RedHatInsights/platform-frontend-ai-dev).

Focused on [insights-rbac](https://github.com/RedHatInsights/insights-rbac) — a Django REST Framework microservice providing Role-Based Access Control for console.redhat.com.

## Architecture

Uses dev-bot as a git submodule. The submodule ships `Dockerfile.runner` which builds the full bot image and runs instance-specific customization hooks from this repo.

```
fabric-access-ai-dev/
├── dev-bot/        # Git submodule (don't modify)
├── setup.sh        # Custom build steps (dnf install, pip install, etc.)
├── instance/       # Extra files COPYed into the image
│   ├── rbac-config/              # Jira-sprint instance
│   │   └── agent/
│   │       ├── instance.yaml        # workflow: jira-sprint
│   │       ├── project-repos.json
│   │       ├── mcp.json
│   │       └── personas/
│   ├── rbac-config-konflux/      # Konflux PR-squash instance
│   ├── rbac-project-coordinator/ # Jira coordinator with an independent planner workflow
│   ├── rbac-config-pr-label/     # GitHub PR-label instance (label: dev-bot)
│   │   └── agent/
│   │       ├── instance.yaml        # workflow: ./workflows/pr-label
│   │       ├── project-repos.json
│   │       └── workflows/pr-label/
│   └── rbac-config-review/       # GitHub review-only instance (label: dev-bot-review)
│       └── agent/
│           ├── instance.yaml        # workflow: ./workflows/review-only
│           ├── project-repos.json   # PR review allowlist
│           ├── review-context.yaml  # related repos and direct Markdown links
│           └── workflows/review-only/
└── README.md
```

One instance = one workflow. `rbac-config` still starts from Jira. Jira implementation PRs use a title that starts with the ticket key, such as `[RHCLOUD-12345] feat: add new feature`. `rbac-config-pr-label` watches open GitHub PRs labeled `dev-bot`, creates a memory-server task, and pushes commits / review suggestions onto that same PR. It adds a bracketed Jira key at the start of a PR title when one is missing, creates and links a Jira Task when a labeled PR has no key, and updates that Jira issue to **Release Pending** when the PR merges.

To run the PR-label instance, deploy a **second** bot with the same image. Do not copy the Jira bot's `BOT_LABEL` or `BOT_INSTANCE_ID`.

| Parameter | Required | Example |
|-----------|----------|---------|
| `BOT_CONFIG_PATH` | yes | `instance/rbac-config-pr-label` |
| `BOT_NAME` | yes | a name distinct from the Jira bot (e.g. `devbot-fabric-access-pr-label`) |
| `BOT_INSTANCE_ID` | yes, **must differ** from the Jira and Konflux bots | e.g. `fabric-access-pr-label` |
| `BOT_PR_LABEL` | no (defaults to `dev-bot`) | GitHub PR label to watch |
| `BOT_LABEL` | ignored by this workflow | leave as-is; it is the Jira ticket label |

The GitHub label is `BOT_PR_LABEL`, not `BOT_LABEL`, so a copied Jira deploy target cannot accidentally search for the Jira label on GitHub.

### Review-only instance

`rbac-config-review` scans the GitHub repositories in its `project-repos.json`
for open PRs carrying the label configured by `review-context.yaml` (currently
`dev-bot-review`). It reviews at most one PR per cycle and publishes a GitHub
summary with inline comments for verified findings. The workflow is read-only
for source code. Its settings, review-context and dimensions YAML, selection
rules, and review stages are documented in the
[review bot README](instance/rbac-config-review/README.md).

The GitHub PR label is `review-context.yaml: review_label`; deployment
`BOT_LABEL` identifies the runner and does not control PR selection. The review
instance configuration lives under `instance/rbac-config-review` and is
deployed separately from the Jira and PR-label instances. This profile handles
GitHub PRs; the `app-interface` GitLab repository needs an MR adapter before it
can enter this queue.

No Dockerfile in this repo — Konflux points at `dev-bot/Dockerfile.runner`.

## Jira project coordinator

`instance/rbac-project-coordinator` is a separate bot instance using the
`project-coordinator` workflow. It discovers RHCLOUD control tickets labeled
`dev-bot-coordinator`, checks the parent and direct children against the
Jira Team ID, and posts project plans and progress on the control ticket. It
never writes code. Implementation stays with the `rbac-config` Jira bot.

Deploy another bot target using `deploy/template.yaml` or
`deploy/rbac-config-template.yaml`, with a distinct `BOT_NAME` and
`BOT_INSTANCE_ID`. Set these parameters for the coordinator target:

| Parameter | Value |
|---|---|
| `BOT_CONFIG_PATH` | `instance/rbac-project-coordinator` |
| `BOT_LABEL` | `dev-bot-coordinator` |
| `BOT_IMPLEMENTATION_LABEL` | The implementation bot's actual Jira label, currently `hcc-ai-platform-accessmanagement` |
| `BOT_JIRA_PROJECT` | `RHCLOUD` |
| `BOT_TEAM_FIELD_ID` | Jira's Team custom field key: `customfield_10001` (also used by this instance's review profiles) |
| `BOT_EXECUTION_ENGINE` | `coordinator` (the runner uses OpenCode for this workflow) |
| `REHOR_OPENCODE_DEPLOYMENT_CONFIG` | `/home/botuser/app/instance/rbac-project-coordinator/opencode-deployment.json` |

No parent or control ticket key is hardcoded. Prepare Jira before the first
coordinator run:

1. **Configure the bot.** Set the Jira Team field ID, implementation label,
   and repository allowlist. The coordinator reads the Team option ID from each
   control ticket; it does not need a fixed Team ID in deployment config. Each
   `repo:<name>` allowed on the control ticket must match a key in
   `project-repos.json`.
2. **Prepare the parent and control ticket.** Use an Epic or supported parent
   issue with a clear project goal. Create exactly one direct child control
   ticket for this Team, set its Jira Team field, and add the `dev-bot-coordinator`
   label. The coordinator reads the Team ID from this field and uses it to
   scope the project; the Team does not need to be re-entered. Leave `repo:`,
   phase, and implementation labels off the control ticket. The coordinator
   proposes the repo setup and sets its first state label when it enrolls.
3. **Prepare implementation tickets.** Make each in-scope ticket a direct
   child of the same parent with the same Team ID. Give each a clear outcome
   and acceptance criteria. Same-Team direct children are treated as project
   work except for the control ticket.
4. **Approve and start the setup.** The coordinator suggests a repository and
   one phase for each implementation ticket. Correct the suggestion if needed,
   then include `approve` or `approved` in a new comment. The comment can contain
   other text; it does not need to be an exact command. After rechecking the
   latest plan, the coordinator applies the repo allowlist and phase labels, then
   starts same-cycle handoff. It does not create or change Jira issue links.
5. **Keep handoff tickets ready.** Tickets must be unassigned and in `New`,
   `Backlog`, `Refinement`, or `To Do`. Sprint membership does not affect
   coordination or handoff. The coordinator chooses each ticket's repo from the
   control ticket's allowlist; make the ticket scope clear enough to identify
   the right repo.

On first enrollment, the coordinator analyzes all same-Team implementation
tickets and posts a proposed order, phase suggestions, dependencies, parallel
groups, repo choices, gaps, and questions on the control ticket. It normally
sets `dev-bot-coordinator:needs-plan` and waits for your approval. A new comment
containing `approve` or `approved` lets it refresh and validate the plan, apply
the proposed repo allowlist and phase labels, and start handoff in the same
cycle. Existing Jira dependency links inform planning, but the coordinator does
not create or change issue links. It sets
`dev-bot-coordinator:active` and releases the unblocked tickets in the earliest
incomplete phase to the implementation bot. It adds `repo:<name>` and the
configured `BOT_IMPLEMENTATION_LABEL` to each implementation ticket at handoff,
using `dev-bot-labels` from `instance/rbac-config-review/agent/review-context.yaml`.
When an implementation ticket has an open PR titled with its Jira key in
brackets, the coordinator adds the same YAML's `review_label` (currently
`dev-bot-review`) so the review bot can pick it up. It reports each handoff and
newly labeled PR on the control ticket. Sprint membership is not an intake gate;
the coordinator never assigns or moves tickets to a sprint.
The control ticket has one coordinator state label:
`dev-bot-coordinator:needs-plan`, `dev-bot-coordinator:active`,
`dev-bot-coordinator:blocked`, or `dev-bot-coordinator:complete`.

The coordinator proposes missing tickets, edits, and closures; it creates a
ticket only when a later comment says `/coord create P-1` for a named proposal.
Approval itself starts plan recheck and same-cycle handoff; no separate start
command is needed. `/coord status` requests a fresh summary; `/coord complete project`
can finish the project after all same-Team implementation tickets are complete.
It never edits or closes implementation tickets.

The preflight is read-only. It starts a coordinator session on first enrollment,
when project data, a matching open PR, or user comments change, or when a
coordinator state label is missing. Otherwise, it skips the project. The
app-interface deployment
currently schedules the instance on weekdays during its configured Europe and
US operating windows; no manual run command is needed. The workflow's
`actions.py` revalidates Jira state before writes and verifies GitHub PR labels
after adding them. New deployment parameters must be supplied by the deployment
target; this repository does not deploy the coordinator by itself.

### Coordination cycle

```mermaid
flowchart TD
    S["Scheduled scan<br/>Jira + saved snapshot"] --> C{"First run, project changed,<br/>or state missing?"}
    C -->|No| W["Wait for next scan"]
    C -->|Yes| R["Read one project<br/>parent, control, same-Team tickets"]
    R --> P["Propose repo and phases"]
    P --> A{"New user comment contains approval?"}
    A -->|No| Q["Answer questions or revise proposal<br/>on the control ticket"]
    A -->|Yes| G["Re-read and validate latest plan<br/>and ticket readiness"]
    G -->|No| B["Report blockers<br/>and wait"]
    G -->|Yes| X["Apply repo allowlist and<br/>phase labels"]
    X --> H["Add implementation labels and<br/>hand off ready tickets"]
    Q --> U["Save snapshot and handled comments"]
    B --> U
    H --> U
    U --> S
```

| Step | Inputs | What happens |
|---|---|---|
| 1. Scan | Project, control label, Team field key, repo allowlist, and saved memory snapshot. | Finds control tickets and their parent issues. Reads each control ticket's Team ID and includes only direct children with that same ID. |
| 2. Detect change | Parent and control details; same-Team child status, labels, links, ownership, and human control comments. | Skips unchanged projects. Starts a cycle for a first run, changed project, or missing state label. |
| 3. Review | Changed project payload: ticket keys, phases, statuses, dependencies, gaps, ready/waiting tickets, allowed repos, and new comments. | Coordinator rereads relevant Jira tickets, answers setup questions, and proposes a repo and phases. |
| 4. Approve and start | Latest proposal and a new user comment containing `approve` or `approved`. | Re-reads and validates the plan, applies repo and phase labels, then starts same-cycle handoff without checking sprint membership. |
| 5. Check readiness and act | Acceptance criteria, ticket dependencies, repo scope, assignees, and statuses. | Reports other plan gaps. For each eligible ticket, validated actions add the selected repo and implementation labels, then hand off the work. Saves the snapshot and handled comment IDs for the next scan. |

On the control ticket, users can request:

```text
/coord plan
/coord planner
/coord status
/coord create P-1
/coord complete project
```

To approve a setup and start implementation, include `approve` or `approved`
in a separate new Jira comment. A planner command requests a technical plan.

`/coord create P-1` applies only to a previously proposed ticket;
`/coord complete project` requires all in-scope tickets to be complete.
During planning, the control comment lists every in-scope ticket with its
current status and phase, plus suggested phases, dependencies, parallel
groups, and repo routing. Existing Jira dependency links inform planning, but
the coordinator never creates or changes issue links. Suggested phases remain
suggestions until approved. Replying `Approved` also works for the current setup
proposal. Jira remains the source of truth; the coordinator rereads relevant
issues before acting.

Every coordinator comment ends with a **Possible next steps** footer. Its
suggestions are generated from the current ticket statuses, plan gaps, approval
requests, and handoff prerequisites; it never asks users to assign tickets to
sprints. When no user action is needed, the footer points to the next scheduled
scan.

## Jira project planner

Comment `/coord planner` on the coordinator control ticket to have the Terra
planner draft a technical plan from its parent's description. The coordinator
scan routes this request to the planner persona. See the
[planner setup and project documentation guide](instance/rbac-project-coordinator/planner/README.md).

## Build

```bash
git submodule update --init --recursive
docker build -f dev-bot/Dockerfile.runner -t fabric-access-ai-dev:local .
```

Or use the helper script:

```bash
./build.sh
```

## Customization

- **setup.sh** — runs as root during build. Install packages, write config, etc.
- **instance/** — files COPYed to `/home/botuser/app/instance/` in the image.

## Updating dev-bot

```bash
cd dev-bot && git pull origin master && cd ..
git add dev-bot
git commit -m "chore: update dev-bot submodule"
```

Konflux also opens automated PRs when dev-bot merges new features.

## Konflux

```yaml
dockerfile: dev-bot/Dockerfile.runner
path-context: .
```
