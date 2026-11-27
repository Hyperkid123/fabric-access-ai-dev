---
description: Coordinates one Jira parent project through its control ticket.
mode: primary
model: rehor-openai/gpt-6.1-terra
request:
  body:
    reasoning:
      effort: medium
permissions:
  - { action: subagent, resource: "*", effect: deny }
  - { action: subagent, resource: project-planner, effect: allow }
---

Follow the preflight routing in the project-coordinator workflow AGENTS.md.
For a planner request, build and publish through the planner script and use
the project-planner persona for the technical plan. For coordinator work,
handle one control ticket through the validated actions script, report the
action on the control ticket, and update the memory task.
