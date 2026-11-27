"""Build a local, revision-pinned context for a Jira planning request."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

REVIEW_AGENT = Path(__file__).resolve().parents[4] / "rbac-config-review" / "agent"
sys.path.insert(0, str(REVIEW_AGENT / "workflows" / "review-only"))
from build_context import ContextError, fetch_doc, update_related_repo  # noqa: E402
from review_config import github_repo_url, load_config  # noqa: E402

MAX_DOC_FILES = 20
MAX_DOC_BYTES = 128 * 1024
MAX_TOTAL_DOC_BYTES = 512 * 1024
SAFE_ERRORS = (ContextError, OSError, ValueError, subprocess.TimeoutExpired)
DOC_DECLARATION = re.compile(r"^[ \t]*project documentation[ \t]*:[ \t]*(.*)$", re.I | re.M)
FILTER_DECLARATION = re.compile(r"^[ \t]*project documentation filter[ \t]*:[ \t]*(.*)$", re.I | re.M)
JIRA_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", re.IGNORECASE | re.ASCII)


def relative_parts(path: str) -> list[str]:
    parts = path.split("/")
    if any(part in {"", ".", ".."} or re.search(r"[\\%?#:\x00-\x1f\x7f]", part) for part in parts):
        raise ValueError("expected a relative path without empty or traversal segments")
    return parts


def docs_location(base: str, *, trusted_gitlab_base: str = "") -> tuple[str, str, list[str], str, str]:
    """Resolve a GitHub or GitLab repository, tree, or blob URL."""
    url = urlsplit(base)
    if (url.scheme != "https" or not url.hostname or url.netloc != url.hostname
            or url.query or url.fragment or url.username or url.password):
        raise ValueError("Project documentation URLs must be HTTPS repository, tree, or blob URLs")
    parts = relative_parts("/".join(unquote(part) for part in url.path.strip("/").split("/")))
    github = url.hostname == "github.com"
    trusted_gitlab_host = urlsplit(trusted_gitlab_base).hostname
    is_gitlab_host = url.hostname == "gitlab.com" or url.hostname.startswith("gitlab.")
    if not github and not is_gitlab_host and url.hostname != trusted_gitlab_host:
        raise ValueError("Project documentation must use GitHub or a GitLab host")
    if github:
        if len(parts) < 2:
            raise ValueError("GitHub URL must identify an owner and repository")
        owner_repo, suffix = parts[:2], parts[2:]
        marker_index = 0
    else:
        marker_index = parts.index("-") if "-" in parts else len(parts)
        owner_repo, suffix = parts[:marker_index], parts[marker_index:]
        if len(owner_repo) < 2:
            raise ValueError("GitLab URL must identify a project repository")
    kind = "tree"
    if suffix:
        if github:
            if suffix[0] not in {"tree", "blob"} or len(suffix) < 2:
                raise ValueError("Use a GitHub repository, tree, or blob URL")
            kind, ref, folder = suffix[0], suffix[1], suffix[2:]
        else:
            if len(suffix) < 3 or suffix[0] != "-" or suffix[1] not in {"tree", "blob"}:
                raise ValueError("Use a GitLab repository, /-/tree, or /-/blob URL")
            kind, ref, folder = suffix[1], suffix[2], suffix[3:]
    else:
        ref, folder = "HEAD", []
    if kind == "blob" and not folder:
        raise ValueError("A blob URL must identify a file")
    if ref.startswith("-"):
        raise ValueError("invalid documentation ref")
    repo = f"https://{url.hostname}/" + "/".join(quote(part, safe="") for part in owner_repo)
    return repo.removesuffix(".git"), ref, folder, "" if github else "/-", kind


def git(directory: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(directory), *args],
        capture_output=True, timeout=90, check=False,
    )
    if result.returncode:
        raise ContextError(f"Git {args[0]} failed (exit {result.returncode})")
    return result.stdout


def jira_reference(location: str) -> str | None:
    """Extract an issue key; the connector, never the pasted URL, reads Jira."""
    if JIRA_KEY.fullmatch(location):
        return location.upper()
    link = re.fullmatch(r"\[[^\]]+\]\((https://[^\s)]+)\)", location)
    url = urlsplit(link[1] if link else location.strip("<>"))
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        return None
    parts = url.path.strip("/").split("/")
    if len(parts) >= 2 and parts[-2] in {"browse", "issues"} and JIRA_KEY.fullmatch(parts[-1]):
        return parts[-1].upper()
    return None


def read_project_docs(
    request: dict, directory: Path, load_jira_document: Callable[[str], dict],
) -> tuple[list[dict], list[str]]:
    repo_labels = request["parent"].get("repo_labels") or []
    control = request.get("control") or {}
    parent = request["parent"]
    # Without repo labels, the control ticket is the only documentation
    # declaration surface. This prevents an undeclared parent brief from
    # becoming implicit planner context.
    if repo_labels:
        sources = [parent, control]
    else:
        sources = [control]
    jira_keys: dict[str, None] = {}
    warnings = []
    git_locations: list[tuple[dict, str, str]] = []
    for source in sources:
        for match in DOC_DECLARATION.findall(source.get("description") or ""):
            location = match.strip().strip("'\"`")
            try:
                key = jira_reference(location)
                if key:
                    jira_keys.setdefault(key, None)
                    continue
                parsed_url = urlsplit(location)
                if parsed_url.scheme:
                    docs_location(location, trusted_gitlab_base=request.get("docs_base", ""))
                else:
                    relative_parts(location)
            except ValueError:
                warnings.append(
                    f"Project documentation in {source.get('key', 'the control ticket')} must be a GitHub/GitLab repository, tree, or blob URL, a relative Git path, a Jira issue key, or a Jira issue URL."
                )
                continue
            git_locations.append((source, location, source.get("description") or ""))

    documents = []
    git_bytes = 0
    for source, location, description in git_locations[:MAX_DOC_FILES]:
        git_request = {**request, "source_description": description}
        git_docs, git_warnings = read_git_docs(git_request, source, location, directory)
        remaining = max(0, MAX_TOTAL_DOC_BYTES - git_bytes)
        selected = []
        for document in git_docs:
            try:
                size = Path(document["path"]).stat().st_size
            except OSError:
                continue
            if len(selected) >= MAX_DOC_FILES - len(documents) or size > remaining:
                warnings.append("Skipped project documentation files because the combined file or text limit was reached.")
                break
            selected.append(document)
            git_bytes += size
            remaining -= size
        git_docs = selected
        documents.extend(git_docs)
        warnings.extend(git_warnings)
    if len(git_locations) > MAX_DOC_FILES:
        warnings.append(f"Read only the first {MAX_DOC_FILES} Git project documentation references.")
    jira_bytes = git_bytes
    available_jira_slots = max(0, MAX_DOC_FILES - len(documents))
    for key in list(jira_keys)[:available_jira_slots]:
        try:
            issue = next((item for item in [*sources, control, parent] if item.get("key") == key), None)
            issue = issue if issue is not None else load_jira_document(key)
            description = issue["description"].strip()
            if not description:
                warnings.append(f"Jira project documentation {key} has an empty description.")
                continue
            content = f"# {key} — {issue['summary']}\n\n{description}\n"
            size = len(content.encode("utf-8"))
            if size > MAX_DOC_BYTES or jira_bytes + size > MAX_TOTAL_DOC_BYTES:
                warnings.append(f"Jira project documentation {key} exceeds the text size limit.")
                continue
            local = directory / f"jira-doc-{key}.txt"
            local.write_text(content, encoding="utf-8")
            document = {"jira_key": key, "path": str(local)}
            url = issue.get("url")
            if isinstance(url, str) and jira_reference(url) == key:
                document["url"] = url
            documents.append(document)
            jira_bytes += size
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Could not load Jira project documentation {key}: {exc}.")
    if len(jira_keys) > available_jira_slots:
        skipped = len(jira_keys) - available_jira_slots
        warnings.append(f"Skipped {skipped} Jira project documentation reference(s) because the combined file limit was reached.")
    return documents, warnings


def read_git_docs(request: dict, source: dict, location: str, directory: Path) -> tuple[list[dict], list[str]]:
    description = request.get("source_description") or source.get("description") or ""
    filters = FILTER_DECLARATION.findall(description)
    pattern = filters[0].strip().strip("'\"`") if filters else request["docs_glob"]
    warnings = []
    try:
        if urlsplit(location).scheme:
            repo, ref, parts, web_prefix, kind = docs_location(
                location, trusted_gitlab_base=request.get("docs_base", ""),
            )
            path = "/".join(parts)
            exact_blob = kind == "blob"
        else:
            if not request["docs_base"]:
                return [], [f"Relative Git project documentation `{location}` needs BOT_PLANNER_DOCS_BASE_URL."]
            repo, ref, folder, web_prefix, kind = docs_location(request["docs_base"])
            if kind != "tree":
                raise ValueError("BOT_PLANNER_DOCS_BASE_URL must identify a repository or directory")
            path = "/".join([*folder, *relative_parts(location)])
            exact_blob = False
        if not pattern:
            raise ValueError("the project documentation filter is empty")
        with tempfile.TemporaryDirectory(prefix="project-docs-") as scratch:
            git_dir = Path(scratch)
            git(git_dir, "init", "--bare", "--quiet")
            git(git_dir, "fetch", "--no-tags", "--depth=1", "--", repo + ".git", ref)
            revision = git(git_dir, "rev-parse", "FETCH_HEAD").decode().strip()
            records = git(git_dir, "ls-tree", "-rlz", revision, *( ["--", path] if path else [] ))
            documents = []
            total = 0
            matched = 0
            omitted = 0
            for entry in records.split(b"\0"):
                if not entry:
                    continue
                header, filename = entry.decode().split("\t", 1)
                mode, kind, blob, size_text = header.split()
                if kind != "blob" or mode not in {"100644", "100755"}:
                    continue
                if exact_blob and filename != path:
                    continue
                if filename != path:
                    if path and not filename.startswith(path + "/"):
                        continue
                    relative = filename[len(path) + 1:] if path else filename
                    if not fnmatchcase(relative, pattern):
                        continue
                matched += 1
                size = int(size_text)
                if (len(documents) >= MAX_DOC_FILES or size > MAX_DOC_BYTES
                        or total + size > MAX_TOTAL_DOC_BYTES):
                    omitted += 1
                    continue
                content = git(git_dir, "cat-file", "blob", blob)
                try:
                    decoded = content.decode("utf-8")
                    if "\0" in decoded:
                        raise ValueError("binary content")
                except ValueError:
                    omitted += 1
                    continue
                local = directory / f"project-doc-{len(documents) + 1}.txt"
                local.write_text(decoded, encoding="utf-8")
                documents.append({
                    "source_path": filename,
                    "path": str(local),
                    "url": f"{repo}{web_prefix}/blob/{revision}/{quote(filename)}",
                    "sha": revision,
                })
                total += size
            if not matched:
                warnings.append(
                    f"Project documentation `{location}` in {source.get('key', 'the control ticket')} was not found "
                    f"or no files matched `{pattern}`."
                )
            if omitted:
                warnings.append(
                    f"Skipped {omitted} project documentation file(s) because of text or size limits."
                )
            return documents, warnings
    except SAFE_ERRORS as exc:
        return [], [*warnings, f"Could not load project documentation `{location}` for {source.get('key', 'the control ticket')}: {exc}."]


def review_sources(labels: list[str], directory: Path) -> tuple[list[dict], list[dict], list[str]]:
    if not labels:
        return [], [], []
    try:
        config = load_config(REVIEW_AGENT / "review-context.yaml")["repositories"]
        allowlist = json.loads((REVIEW_AGENT / "project-repos.json").read_text())
    except SAFE_ERRORS as exc:
        return [], [], [f"Could not load review context configuration: {exc}."]

    repos: dict[str, dict] = {}
    docs: dict[str, str] = {}
    warnings = []
    for label in labels:
        key = label.removeprefix("repo:")
        entry = allowlist.get(key)
        if not entry:
            warnings.append(f"Repository label `{label}` is absent from the review repository allowlist.")
            continue
        profile = config.get(key, {})
        entries = [
            {"id": key, "url": entry["upstream"], "relationship": f"Selected by {label}"},
            *profile.get("related", []),
        ]
        for item in entries:
            repo = github_repo_url(item["url"])
            result = repos.setdefault(repo, {"repo": repo, "relationships": []})
            relationship = item["relationship"]
            if relationship not in result["relationships"]:
                result["relationships"].append(relationship)
        for item in profile.get("docs", []):
            docs.setdefault(item["url"], item["id"])

    def checkout(index: int, entry: dict) -> dict:
        repo = entry["repo"]
        url = f"https://github.com/{repo}"
        destination = directory / "repos" / str(index)
        sha = update_related_repo(url, destination)
        return {**entry, "path": str(destination), "sha": sha, "url": f"{url}/tree/{sha}"}

    def fetch(index: int, url: str, name: str) -> dict:
        content, permalink = fetch_doc(url)
        if len(content.encode()) > MAX_DOC_BYTES:
            raise ValueError("documentation exceeds 128 KiB")
        path = directory / f"review-doc-{index}.txt"
        path.write_text(content, encoding="utf-8")
        return {"id": name, "path": str(path), "url": permalink}

    with ThreadPoolExecutor(max_workers=4) as pool:
        repo_jobs = [(item["repo"], pool.submit(checkout, i, item)) for i, item in enumerate(repos.values())]
        doc_jobs = [(url, pool.submit(fetch, i, url, name)) for i, (url, name) in enumerate(docs.items())]
        results = []
        for jobs in (repo_jobs, doc_jobs):
            collection = []
            for name, job in jobs:
                try:
                    collection.append(job.result())
                except SAFE_ERRORS as exc:
                    warnings.append(f"Could not load review context from `{name}`: {exc}.")
            results.append(collection)
    return results[0], results[1], warnings


def build_context(
    request: dict, root: Path = Path("data/planner-context"), *, load_jira_document: Callable[[str], dict],
) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=request["control_key"] + "-", dir=root)).resolve()
    project_docs, project_warnings = read_project_docs(request, directory, load_jira_document)
    repos, review_docs, repo_warnings = review_sources(request["parent"]["repo_labels"], directory)
    warnings = [*request["warnings"], *project_warnings, *repo_warnings]
    context_available = bool(project_docs or repos or review_docs)
    if not context_available:
        warnings.append("No project documentation or selected repository context was available; no technical plan will be drafted.")
    manifest = {
        "control_key": request["control_key"],
        "comment_id": request["comment_id"],
        "control": request["control"],
        "parent": request["parent"],
        "implementation_tickets": request["implementation_tickets"],
        "ticket_snapshot_hash": request["ticket_snapshot_hash"],
        "available_repositories": request["available_repositories"],
        "focus": request["focus"],
        "request_id": request["request_id"],
        "project_documents": project_docs,
        "repo_checkouts": repos,
        "review_documents": review_docs,
        "context_available": context_available,
        "warnings": warnings,
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status": "context_ready" if context_available else "context_missing", "manifest": str(manifest_path), "warnings": warnings}
