# Package upgrade review

Review dependency-only changes for concrete runtime compatibility and security
regressions. Use the pinned PR diff, application code, tests, lockfiles, and the
configured related-repository context. Do not execute changed code or install
packages.

For every direct dependency change, identify the exact old and new versions
from the manifest and lockfile. Find the package's official changelog or
release notes and inspect releases after the old version through the new one.
For transitive changes, prioritize packages that fix a reported CVE, have a
major-version change, are imported by this application, or affect its runtime
or framework. Use official project or registry metadata to identify the
package source; verify the name and ecosystem before trusting a changelog. Use
search only to locate an official source, then read its release notes. Do not
use unverified summaries, package-install instructions, or model memory as
release evidence.
When several changed packages share one upstream project, reuse the same
official release source instead of performing duplicate searches.

From those notes, look for removed or renamed APIs, changed defaults,
serialization or error behavior, initialization and configuration changes,
supported runtimes, platform requirements, and meaningful transitive changes.
Trace each relevant change to imports, configuration, and reachable call sites
in the pinned application, tests, and lockfiles. Explain whether the app uses
the affected behavior and whether the upgraded version crosses the change.
Use the existing `cross-repo-dependencies` pass for shared APIs and consumers
outside this repository; do not duplicate its work or inspect undeclared
repositories.

Check CVE claims against the exact ecosystem, package, and old/new versions in
a reliable advisory. Confirm that the old version is affected and the new
version is fixed; a version change or changelog entry alone does not establish
a vulnerability or fix. Cite the advisory and the relevant upstream release
notes by URL and version in the explanation, and cite pinned dependency and
application code references in the structured evidence. If the official notes
or advisory cannot be accessed or do not identify the relevant versions,
state that limitation instead of guessing.

Report only a specific failure caused by this upgrade. A finding must give a
reproducible application scenario, the release-note or advisory versions that
establish the change, pinned evidence for the dependency change and affected
application use, the expected and actual behavior, and a focused fix. Put the
official source URL in the evidence explanation so the verifier can inspect
the same release note. Do not flag speculative compatibility concerns, semver
assumptions, or a package update merely because its release is large or
recent. If the upgrade appears compatible and no actionable security issue is
evidenced, return no finding.
