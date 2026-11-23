# Review bot cost optimization design

**Status:** Proposal, with initial optimizations implemented
**Scope:** `instance/rbac-config-review`
**Decision:** Measure full review cost by stage, then compare lower-cost review plans against the current `high` plan before changing the default.

## Goals and boundaries

- Reduce AI cost per completed review while preserving detection of correctness and security issues.
- Attribute spend to workflow stages, dimensions, and models so optimization targets observed costs.
- Keep review quality, evidence requirements, and publication gates intact.
- Keep the change independent of PR #185's risk-label behavior. A `low-risk` label must not skip security or correctness review and does not authorize auto-merge.

This design covers AI token cost. Deterministic GitHub, Git, and Python work remains outside the AI cost target.

## Current cost shape

The dimensional review catalog defaults to `high`, which selects every applicable built-in dimension. The current catalog has 18 default-enabled dimensions; repository-required skills can increase that count. The `light` and `medium` presets already exist in [dimensions.yaml](agent/skills/dimensional-review/dimensions.yaml). Each selected pass receives the pinned PR context, so repeated context across dimension calls is the leading cost hypothesis.

A full review also uses the Terra review runner, a Luna risk assessor, result merging, missing-context detection when applicable, and a Luna verifier for proposed findings. Dimension workers run in plan-ordered batches of up to four. Existing documentation identifies the dimension workers as the likely largest combined spend, but the exact stage shares are not known.

The shared cost tracker currently records cycle USD, model usage, input/output tokens, and cache read/write tokens. Its database and metrics aggregate by model and label, not review stage or dimension. Confirm whether the SDK's cycle result includes subagent usage; if it does not, cycle totals alone undercount the review. One historical run estimated about $0.83 for 16 dimension passes and produced no confirmed findings. Treat that as an example, not a baseline.

## Measurement design

Record one usage event for each actual model invocation, then associate it with its parent review cycle. Do not divide cycle totals across stages by estimate. Reconcile the sum of invocation events against the authoritative cycle total and report any unattributed difference.

Each event should contain:

| Field | Purpose |
|---|---|
| `cycle_id`, repository, PR number, head SHA | Join events to one pinned review and avoid mixing re-reviews |
| `stage`, optional `dimension` | Attribute to runner, risk assessor, dimension, merge, detector, verifier, or follow-up answer |
| model and configured reasoning effort | Compare existing Terra/Luna assignments |
| review level, plan/catalog revision, prompt revision | Explain cost and quality changes between plans |
| input, output, cache-read, and cache-write tokens | Compare context size and cache effectiveness |
| provider-reported USD and rate/pricing revision | Preserve the charged amount and make pricing changes auditable |
| duration, outcome, retry count | Track latency, failures, and retry spend |

Log identifiers and token counts only; do not store prompts, diffs, source excerpts, or model responses in cost events. Missing provider usage remains unknown instead of being inferred. Keep low-cardinality stage and model labels in Prometheus; use the database/dashboard for PR-level detail.

The initial dashboard should show median and p90 cost per full review, cost by stage and dimension, input/output/cache tokens by model, cache-read share, failed/retried invocation spend, and cost split by full review, re-review, verification, and comment-answer cycles. Report both cost per review and cost per verifier-confirmed actionable finding; the latter is a quality-sensitive supporting metric, not a target to game by suppressing findings.

## Optimization candidates

Apply candidates in this order so each experiment has a measurable effect and a clear quality risk.

### 1. Establish a trustworthy baseline

Collect at least 30 representative completed full reviews or four weeks of data, whichever takes longer. Separate full reviews from re-reviews and comment replies. Stratify by repository, change size, language, and documentation-only status. Check that per-invocation events reconcile with cycle totals before using the breakdown to make decisions.

### 2. Reduce repeated context cost

First measure cache-read and cache-write tokens. If the provider/proxy supports prompt caching for these models, preserve a stable common prefix and put dimension-specific instructions and evidence after it. Measure cache hit rates and actual USD before expanding this path.

The initial implementation now gives each worker the guide and manifest paths so it can read the pinned sources needed for its dimension. Measure actual input tokens and finding coverage before narrowing access or generating summaries. Keep the full pinned diff and source checkout available. Cross-cutting checks such as security, architecture, and cross-repository compatibility retain access to all relevant changed code and contracts. Do not truncate evidence to meet a token budget.

### 3. Select dimensions from explicit coverage rules

Keep correctness, security, and repository-required skills mandatory at every review level. Select additional specialists from deterministic signals such as changed file patterns, languages, database/API/configuration changes, and confirmed cross-repository impact. Keep the broad plan for high-risk or materially uncertain changes. Keep the risk assessor independent of findings and do not use its `low` label alone to reduce review coverage.

Evaluate the existing `medium` preset against `high` as a candidate; do not change the current default until the evaluation gate below passes. Make any rule additions explicit in the catalog and document which change signals select each dimension. Keep an operator override for a full `high` review.

### 4. Bound merge and verification context

The initial implementation merges structured per-dimension results in Python and skips a new-finding verifier call when there are no proposed findings. Keep verification for every proposed finding, addressed prior finding, and custom formatting decision. Preserve each finding's pinned evidence and relevant ledger entry.

### 5. Tune model assignments using paired evidence

Use the existing `model_preference` catalog as the only routing control. Compare Terra and Luna on the same representative PR set for candidate dimensions, then review both cost and finding quality. Keep current assignments for correctness, security-sensitive, and high-judgment work unless paired results show equal coverage. Do not change model or reasoning effort ad hoc during a live review.

## Evaluation and rollout gates

Use pinned historical PR snapshots for a paired replay of the current `high` plan and each candidate. Where the historical set lacks known findings, add a curated regression set with confirmed correctness and security defects. Compare:

- Total AI USD per review and cost by stage/dimension.
- Recall of known correctness/security blockers and actionable-finding precision.
- Verifier-confirmed findings, author-confirmed fixes, and review publication failures.
- Median/p90 latency, failed calls, and retry spend.

A candidate is eligible for a limited rollout only if it lowers median full-review AI cost by at least 20%, misses no known correctness/security blocker in the regression set, and reduces actionable-finding precision by no more than five percentage points. Track p90 cost and latency as guardrails. These are proposed initial thresholds; review them after the baseline is available.

Roll out behind an explicit configuration switch, compare the canary with the unchanged `high` plan, and retain a one-step rollback to `high`. Roll back on any missed known blocker, material publication failure, or unexplained rise in p90 spend. Keep branch protection and human approvals as the merge controls throughout.

## Initial implementation in the review workflow

The first optimization change implements these items:

- Make `learning-extraction` opt-in for full reviews and expose it as the exact `/review-bot learning-extraction` command on review comments.
- Pass each dimension worker a guide path and a pinned manifest path.
- Merge dimension results, deduplicate findings, and rank them in Python.
- Skip the new-finding verifier call when there are no findings to challenge.

The command uses one focused Luna pass and replies with reusable lessons and pinned evidence. It does not publish review findings or modify the finding ledger.

## Remaining delivery sequence

1. Add per-invocation stage attribution and validate reconciliation with cycle totals.
2. Build the baseline dashboard and record the representative evaluation set.
3. Measure the prompt and manifest-path changes, then run cache experiments without changing dimension coverage.
4. Replay `medium` and explicit path-based selection rules against `high`.
5. Change the default only after the evaluation and canary gates pass.

The full-review default remains `high` until the evaluation and canary gates pass.
