# Fabric Access project coordinator

This instance runs the Jira coordinator and the technical planner. Follow
the routing in `workflows/project-coordinator/AGENTS.md`. Do not implement
code or open PRs. Use `BOT_INSTANCE_ID` for coordinator memory task isolation.
The Team field ID is configured as `BOT_TEAM_FIELD_ID`. Derive the Team ID
from each control ticket, record it on that task, and include only direct
children of its parent with that same Team ID in coordination.
