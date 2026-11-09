# Observability review

Assess whether the changed behavior needs operational visibility: for example,
whether a new failure mode, important workflow, dependency, latency, or volume
would be difficult to detect or diagnose with existing signals. Inspect relevant
metrics, instrumentation, and Grafana dashboards in the target repository and
the declared, pinned review context when available.

Recommend a metric or dashboard change only when it closes a concrete visibility
gap and its operational benefit justifies the cost. Prefer existing metrics and
dashboard patterns; consider label cardinality, privacy, and noise. If existing
signals already cover the change, no finding is needed. Do not assume a
dashboard or monitoring configuration exists outside the available context.
When evidence supports a finding, identify the missing signal, the affected
operator decision, and a practical metric or dashboard change, cite pinned code
or configuration, and anchor the finding to a changed PR line.
