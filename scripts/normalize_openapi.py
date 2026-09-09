#!/usr/bin/env python3
"""Normalize the Simpplr OpenAPI specs so strict validators accept them.

Both source specs use `$ref` pointers that point *into* `#/paths/...` — a
back-reference style that reuses one operation's response or schema fragment
from another operation. That is legal JSON Pointer but strict OpenAPI
validators reject it (and the pointers containing `{pathParam}` braces are not
resolvable as URI fragments at all), so Mintlify refuses to build the API
reference from them.

This script resolves every such pointer and hoists the target into
`components/responses` (for Response Objects) or `components/schemas` (for
schema fragments), rewriting each `$ref` to point at the hoisted component.
The result is semantically identical to the input — only the location of the
shared definitions changes.

Usage:
    python3 scripts/normalize_openapi.py SOURCE.json OUTPUT.json
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

PATH_REF_PREFIX = "#/paths/"
MAX_PASSES = 10


def decode_pointer_token(token: str) -> str:
    """Decode a single JSON Pointer token (RFC 6901: ~1 -> /, ~0 -> ~)."""
    return token.replace("~1", "/").replace("~0", "~")


def split_pointer(ref: str) -> list[str]:
    """Split a local `#/a/b/c` ref into decoded pointer tokens."""
    if not ref.startswith("#/"):
        raise ValueError(f"not a local ref: {ref}")
    return [decode_pointer_token(t) for t in ref[2:].split("/")]


def resolve_pointer(document: Any, tokens: list[str]) -> Any:
    """Walk `document` following decoded pointer `tokens`."""
    node = document
    for token in tokens:
        if isinstance(node, list):
            node = node[int(token)]
        else:
            node = node[token]
    return node


def iter_refs(node: Any):
    """Yield every dict that is a `$ref` object anywhere in the tree."""
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node
        for value in node.values():
            yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


def component_name(tokens: list[str]) -> str:
    """Build a stable, valid component key from pointer tokens.

    Component keys must match ^[a-zA-Z0-9._-]+$, so every other character is
    dropped after splitting the pointer into words.
    """
    skip = {
        "paths",
        "content",
        "application/json",
        "schema",
        "properties",
        "requestBody",
        "responses",
        "items",
        "oneOf",
        "allOf",
        "anyOf",
    }
    words: list[str] = []
    for token in tokens:
        if token in skip:
            continue
        for word in re.split(r"[^A-Za-z0-9]+", token):
            if word:
                words.append(word[:1].upper() + word[1:])
    name = "".join(words) or "Shared"
    return re.sub(r"[^A-Za-z0-9._-]", "", name)


def is_response_pointer(tokens: list[str]) -> bool:
    """True when the pointer addresses a Response Object (`responses/<code>`)."""
    return len(tokens) >= 2 and tokens[-2] == "responses"


def resolve_oauth_flow_urls(document: dict) -> list[str]:
    """Replace `{var}` templates in OAuth flow URLs with server-variable defaults.

    Only `servers[].url` may contain `{variable}` templates in OpenAPI. The
    `authorizationUrl` / `tokenUrl` / `refreshUrl` fields are plain URIs, so a
    template there fails `format: uri` validation. The substitution values come
    from the spec's own `servers[].variables[].default`, so no new information
    is introduced. Returns a list of human-readable change descriptions.
    """
    defaults: dict[str, str] = {}
    for server in document.get("servers", []):
        for name, spec in (server.get("variables") or {}).items():
            if "default" in spec:
                defaults.setdefault(name, str(spec["default"]))

    changes: list[str] = []
    schemes = document.get("components", {}).get("securitySchemes", {})
    for scheme_name, scheme in schemes.items():
        for flow_name, flow in (scheme.get("flows") or {}).items():
            for field in ("authorizationUrl", "tokenUrl", "refreshUrl"):
                url = flow.get(field)
                if not isinstance(url, str) or "{" not in url:
                    continue
                resolved = url
                for var, value in defaults.items():
                    resolved = resolved.replace("{" + var + "}", value)
                if "{" in resolved:
                    changes.append(
                        f"WARNING {scheme_name}.{flow_name}.{field}: "
                        f"unresolved template remains in {resolved!r}"
                    )
                    continue
                flow[field] = resolved
                changes.append(
                    f"{scheme_name}.{flow_name}.{field}: {url!r} -> {resolved!r}"
                )
    return changes


def sanitize_operation_summaries(document: dict) -> list[str]:
    """Rewrite `&` and `/` in operation summaries to plain words.

    Mintlify derives each generated reference page's slug from the operation
    summary. An `&` prevents the page from being generated at all (verified:
    the User spec's `Get Access & Refresh Token` was silently dropped from the
    nav), and a `/` is stripped rather than separated, producing slugs like
    `publishunpublish-content`. Substituting the words keeps the meaning and
    makes the slug predictable. Returns a list of change descriptions.
    """
    changes: list[str] = []
    methods = ("get", "post", "put", "patch", "delete", "head", "options")

    for path, item in (document.get("paths") or {}).items():
        for method, operation in item.items():
            if method not in methods or not isinstance(operation, dict):
                continue
            summary = operation.get("summary")
            if not isinstance(summary, str):
                continue

            rewritten = summary.replace("&", " and ").replace("/", " or ")
            rewritten = re.sub(r"\s+", " ", rewritten).strip()
            if rewritten != summary:
                operation["summary"] = rewritten
                changes.append(
                    f"{method.upper()} {path} summary: "
                    f"{summary!r} -> {rewritten!r}"
                )
    return changes


def normalize(document: dict) -> tuple[dict, dict[str, str]]:
    """Hoist all `#/paths/...` refs into components. Returns (doc, mapping)."""
    components = document.setdefault("components", {})
    mapping: dict[str, str] = {}
    used_names: set[str] = set()
    for bucket in ("schemas", "responses"):
        used_names.update(components.get(bucket, {}).keys())

    for _ in range(MAX_PASSES):
        pending = sorted(
            {
                node["$ref"]
                for node in iter_refs(document)
                if node["$ref"].startswith(PATH_REF_PREFIX)
                and node["$ref"] not in mapping
            }
        )
        if not pending:
            break

        for ref in pending:
            tokens = split_pointer(ref)
            target = resolve_pointer(document, tokens)
            bucket = "responses" if is_response_pointer(tokens) else "schemas"

            base = component_name(tokens)
            name = base
            suffix = 2
            while name in used_names:
                name = f"{base}{suffix}"
                suffix += 1
            used_names.add(name)

            # Deep-copy so later rewrites of the original location cannot
            # mutate the hoisted definition.
            components.setdefault(bucket, {})[name] = json.loads(
                json.dumps(target)
            )
            mapping[ref] = f"#/components/{bucket}/{name}"

        for node in iter_refs(document):
            if node["$ref"] in mapping:
                node["$ref"] = mapping[node["$ref"]]
    else:
        raise RuntimeError("ref graph did not settle; possible pointer cycle")

    remaining = [
        node["$ref"]
        for node in iter_refs(document)
        if node["$ref"].startswith(PATH_REF_PREFIX)
    ]
    if remaining:
        raise RuntimeError(f"unresolved path refs remain: {remaining[:3]}")

    return document, mapping


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2

    source, output = argv[1], argv[2]
    with open(source) as handle:
        document = json.load(handle)

    document, mapping = normalize(document)
    url_changes = resolve_oauth_flow_urls(document)
    summary_changes = sanitize_operation_summaries(document)

    with open(output, "w") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")

    print(f"{source} -> {output}")
    print(f"  hoisted {len(mapping)} shared definition(s) into components")
    for change in url_changes:
        print(f"  {change}")
    for change in summary_changes:
        print(f"  {change}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
