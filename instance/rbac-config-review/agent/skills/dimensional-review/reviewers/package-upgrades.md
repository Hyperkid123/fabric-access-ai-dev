# Package upgrade review

Review dependency-only changes for concrete runtime compatibility and security
regressions. Use the pinned PR diff, application code, tests, lockfiles, and the
configured related-repository context. Do not execute changed code or install
packages.

For each changed dependency, identify the old and new version from the diff
when available. Check whether the new version changes APIs, defaults,
serialization, error behavior, supported runtimes, platform requirements, or
transitive dependencies used by this application. Trace affected imports and
call sites in the pinned source. Use the existing `cross-repo-dependencies`
pass for shared APIs and consumers outside this repository; do not duplicate
its work or inspect undeclared repositories.

Check CVE claims against exact package and version evidence. A version change
alone does not establish a vulnerability or fix. Cite a reliable advisory or
upstream release note only when it is included in pinned context and it
identifies the affected and fixed versions. If no reliable advisory evidence
is available, report that limitation rather than claiming a CVE.

Report only a specific failure caused by this upgrade. A finding must give a
reproducible application scenario, pinned evidence for the dependency change
and affected use, the expected and actual behavior, and a focused fix. Do not
flag speculative compatibility concerns, semver assumptions, or a package
update merely because its release is large or recent. If the upgrade appears
compatible and no actionable security issue is evidenced, return no finding.
