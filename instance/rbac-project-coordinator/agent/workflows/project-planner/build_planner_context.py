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


def docs_location(base: str) -> tuple[str, str, list[str], str]:
    """Resolve a trusted GitHub or GitLab base URL to repository, ref, and folder."""
    url = urlsplit(base)
    if (url.scheme != "https" or not url.hostname or url.netloc != url.hostname
            or url.query or url.fragment):
        raise ValueError("BOT_PLANNER_DOCS_BASE_URL must be an HTTPS Git repository or tree URL")
    parts = relative_parts(unquote(url.path.strip("/")))
    github = url.hostname == "github.com"
    marker = "tree" if github else "-"
    if github and len(parts) > 2:
        owner_repo, tree = parts[:2], parts[2:]
    elif not github and marker in parts:
        index = parts.index(marker)
        owner_repo, tree = parts[:index], parts[index + 1:]
    else:
        owner_repo, tree = parts, []
    if len(owner_repo) < 2:
        raise ValueError("the docs base must identify a Git repository")
    if tree and (len(tree) < 2 or tree[0] != "tree"):
        raise ValueError("use a repository root or a tree URL for the docs base")
    if not github and marker in parts and not tree:
        raise ValueError("a GitLab tree URL must include a ref")
    ref, folder = (tree[1], tree[2:]) if tree else ("HEAD", [])
    if ref.startswith("-"):
        raise ValueError("invalid documentation ref")
    repo = f"https://{url.hostname}/" + "/".join(quote(part, safe="") for part in owner_repo)
    return repo.removesuffix(".git"), ref, folder, "" if github else "/-"


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
    # Preserve the Epic's Git-path precedence and allow Jira references from
    # both descriptions. The labeled request can also be the Epic itself.
    sources = {item["key"]: item for item in (request["epic"], request["issue"]) if item is not None}
    git_path = None
    jira_keys: dict[str, None] = {}
    warnings = []
    for source in sources.values():
        for match in DOC_DECLARATION.findall(source["description"]):
            location = match.strip().strip("'\"`")
            try:
                key = jira_reference(location)
                if key:
                    jira_keys.setdefault(key, None)
                    continue
                relative_parts(location)
            except ValueError:
                warnings.append(
                    f"Project documentation in {source['key']} must be a relative Git path, "
                    "Jira issue key, or Jira issue URL."
                )
                continue
            if git_path is None:
                git_path = (source, location)
            else:
                warnings.append(f"Skipped additional Git documentation path `{location}` in {source['key']}.")

    documents = []
    if git_path is not None:
        git_docs, git_warnings = read_git_docs(request, *git_path, directory)
        documents.extend(git_docs)
        warnings.extend(git_warnings)
    jira_bytes = 0
    for key in list(jira_keys)[:MAX_DOC_FILES]:
        try:
            issue = sources[key] if key in sources else load_jira_document(key)
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
    if len(jira_keys) > MAX_DOC_FILES:
        warnings.append(f"Read only the first {MAX_DOC_FILES} Jira project documentation references.")
    return documents, warnings


def read_git_docs(request: dict, source: dict, location: str, directory: Path) -> tuple[list[dict], list[str]]:
    filters = FILTER_DECLARATION.findall(source["description"])
    pattern = filters[0].strip().strip("'\"`") if filters else request["docs_glob"]
    warnings = []
    if not request["docs_base"]:
        return [], [f"Git project documentation `{location}` needs BOT_PLANNER_DOCS_BASE_URL."]

    try:
        repo, ref, folder, web_prefix = docs_location(request["docs_base"])
        path = "/".join([*folder, *relative_parts(location)])
        if not pattern:
            raise ValueError("the project documentation filter is empty")
        with tempfile.TemporaryDirectory(prefix="project-docs-") as scratch:
            git_dir = Path(scratch)
            git(git_dir, "init", "--bare", "--quiet")
            git(git_dir, "fetch", "--no-tags", "--depth=1", "--", repo + ".git", ref)
            revision = git(git_dir, "rev-parse", "FETCH_HEAD").decode().strip()
            records = git(git_dir, "ls-tree", "-rlz", revision, "--", path)
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
                if filename != path:
                    if not filename.startswith(path + "/") or not fnmatchcase(filename[len(path) + 1:], pattern):
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
                    f"Project documentation `{location}` in {source['key']} was not found "
                    f"or no files matched `{pattern}`."
                )
            if omitted:
                warnings.append(
                    f"Skipped {omitted} project documentation file(s) because of text or size limits."
                )
            return documents, warnings
    except SAFE_ERRORS as exc:
        return [], [*warnings, f"Could not load project documentation `{location}` for {source['key']}: {exc}."]


def review_sources(labels: list[str], directory: Path) -> tuple[list[dict], list[dict], list[str]]:
    if not labels:
        return [], [], ["No repo: labels were found for selecting repository context."]
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
    directory = Path(tempfile.mkdtemp(prefix=request["issue"]["key"] + "-", dir=root)).resolve()
    project_docs, project_warnings = read_project_docs(request, directory, load_jira_document)
    source = request["epic"] or request["issue"]
    repos, review_docs, repo_warnings = review_sources(source["repo_labels"], directory)
    warnings = [*request["warnings"], *project_warnings, *repo_warnings]
    manifest = {
        "issue": request["issue"],
        "epic": request["epic"],
        "request_id": request["request_id"],
        "project_documents": project_docs,
        "repo_checkouts": repos,
        "review_documents": review_docs,
        "warnings": warnings,
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status": "context_ready", "manifest": str(manifest_path), "warnings": warnings}
