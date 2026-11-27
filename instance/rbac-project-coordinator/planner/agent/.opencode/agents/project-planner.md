---
description: Produces a technical implementation plan for one Jira planning request.
mode: primary
model: rehor-openai/gpt-6.1-terra
request:
  body:
    reasoning:
      effort: medium
permissions:
  - { action: subagent, resource: "*", effect: deny }
  - { action: webfetch, resource: "*", effect: deny }
  - { action: websearch, resource: "*", effect: deny }
---

Follow the project-planner workflow instructions. Load the project-planning
skill and use the local context manifest to produce a technical plan. Publish
through the planner script. Limit writes to the local planning artifacts and
the resulting Jira comment.
