from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- BEGIN CODE-DERIVED ENDPOINTS -->"
END = "<!-- END CODE-DERIVED ENDPOINTS -->"

FEATURE_DOCS = {
	"accounts": "docs/features/accounts/api.md",
	"activity": "docs/features/activity/api.md",
	"ads": "docs/features/ads/api.md",
	"analytics_pipeline": "docs/features/analytics/api.md",
	"auth": "docs/features/authentication.md",
	"calls": "docs/features/calls/api.md",
	"catalog": "docs/features/catalog/api.md",
	"chat": "docs/features/chat/api.md",
	"diagnostics": "docs/features/diagnostics/api.md",
	"live": "docs/features/live/api.md",
	"localization": "docs/features/localization/api.md",
	"maps": "docs/features/maps/api.md",
	"media": "docs/features/media/api.md",
	"notifications": "docs/features/notifications/api.md",
	"reports": "docs/features/reports/api.md",
	"reviews": "docs/features/reviews/api.md",
	"saved_search": "docs/features/saved-search/api.md",
	"search_ranking": "docs/features/search-ranking/api.md",
	"sellers": "docs/features/sellers/api.md",
	"shorts": "docs/features/shorts/api.md",
	"social": "docs/features/social/api.md",
	"verification": "docs/features/verification/api.md",
	"wishlist": "docs/features/wishlist/api.md",
}

PLATFORM_DOCS = {
	"livekit": "docs/features/live/livekit.md",
	"moderation": "docs/production/content-moderation-service.md",
	"notification_delivery": "docs/production/notification-delivery-service.md",
	"video_processing": "docs/production/video-processing-service.md",
	"metrics": "docs/api/internal.md",
}

DOMAIN_LABELS = {
	"accounts": "Accounts",
	"activity": "Activity",
	"ads": "Ads",
	"analytics_pipeline": "Analytics Pipeline",
	"auth": "Auth",
	"calls": "Calls",
	"catalog": "Catalog",
	"chat": "Chat",
	"diagnostics": "Diagnostics",
	"live": "Live",
	"livekit": "LiveKit",
	"localization": "Localization",
	"maps": "Maps",
	"media": "Media",
	"metrics": "Private Metrics",
	"moderation": "Moderation",
	"notification_delivery": "Notification Delivery",
	"notifications": "Notifications",
	"reports": "Reports",
	"reviews": "Reviews",
	"saved_search": "Saved Search",
	"search_ranking": "Search/Ranking",
	"sellers": "Sellers",
	"shorts": "Shorts",
	"social": "Social",
	"verification": "Verification",
	"video_processing": "Video Processing",
	"wishlist": "Wishlist",
}


@dataclass(frozen=True)
class Endpoint:
	domain: str
	function: str
	route: str
	methods: tuple[str, ...]
	allow_guest: bool
	source: str
	versioned: bool

	@property
	def method_label(self) -> str:
		return "/".join(self.methods) if self.methods else "Any*"

	@property
	def access_label(self) -> str:
		return "Guest allowed" if self.allow_guest else "Session required"

	@property
	def audience_label(self) -> str:
		if self.domain == "diagnostics":
			return "Admin"
		if self.domain == "metrics":
			return "Private monitoring"
		if self.domain == "livekit" and self.function == "handle_webhook":
			return "Provider webhook"
		if self.function == "handle_callback" and self.domain in {
			"analytics_pipeline",
			"moderation",
			"notification_delivery",
			"search_ranking",
			"video_processing",
		}:
			return "Signed callback"
		return "Client"


def _whitelist_metadata(decorator: ast.expr) -> tuple[bool, tuple[str, ...]] | None:
	if isinstance(decorator, ast.Attribute):
		if isinstance(decorator.value, ast.Name) and decorator.value.id == "frappe" and decorator.attr == "whitelist":
			return False, ()
		return None
	if not isinstance(decorator, ast.Call):
		return None
	func = decorator.func
	if not (
		isinstance(func, ast.Attribute)
		and isinstance(func.value, ast.Name)
		and func.value.id == "frappe"
		and func.attr == "whitelist"
	):
		return None

	allow_guest = False
	methods: tuple[str, ...] = ()
	for keyword in decorator.keywords:
		if keyword.arg == "allow_guest" and isinstance(keyword.value, ast.Constant):
			allow_guest = bool(keyword.value.value)
		elif keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
			methods = tuple(
				str(item.value)
				for item in keyword.value.elts
				if isinstance(item, ast.Constant) and isinstance(item.value, str)
			)
	return allow_guest, methods


def _discover_file(path: Path, *, domain: str, versioned: bool) -> list[Endpoint]:
	relative_module = path.relative_to(ROOT).with_suffix("")
	if path.name == "__init__.py":
		relative_module = relative_module.parent
	module = ".".join(relative_module.parts)
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	found: list[Endpoint] = []
	for node in tree.body:
		if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
			continue
		metadata = next((value for decorator in node.decorator_list if (value := _whitelist_metadata(decorator))), None)
		if metadata is None:
			continue
		allow_guest, methods = metadata
		found.append(
			Endpoint(
				domain=domain,
				function=node.name,
				route=f"/api/method/{module}.{node.name}",
				methods=methods,
				allow_guest=allow_guest,
				source=str(path.relative_to(ROOT)),
				versioned=versioned,
			)
		)
	return found


def discover_endpoints() -> list[Endpoint]:
	endpoints: list[Endpoint] = []
	for path in sorted((ROOT / "aos/api/v1").glob("*/__init__.py")):
		endpoints.extend(_discover_file(path, domain=path.parent.name, versioned=True))
	endpoints.extend(_discover_file(ROOT / "aos/api/metrics.py", domain="metrics", versioned=False))
	return sorted(endpoints, key=lambda item: (item.domain, item.function))


def _doc_path(domain: str) -> str:
	try:
		return FEATURE_DOCS.get(domain) or PLATFORM_DOCS[domain]
	except KeyError as exc:
		raise RuntimeError(f"No documentation owner configured for API domain {domain!r}") from exc


def _relative_link(from_path: str, to_path: str) -> str:
	from_dir = (ROOT / from_path).parent
	return Path(os.path.relpath(ROOT / to_path, from_dir)).as_posix()


def render_inventory(domain: str, endpoints: list[Endpoint], doc_path: str) -> str:
	domain_endpoints = [endpoint for endpoint in endpoints if endpoint.domain == domain]
	lines = [
		BEGIN,
		"## Endpoint inventory (code-derived)",
		"",
		"This table is generated from the current `@frappe.whitelist` declarations. Business semantics are documented below; do not hand-edit this inventory.",
		"",
		"| Endpoint | HTTP | Decorator access | Audience |",
		"|---|---|---|---|",
	]
	for endpoint in domain_endpoints:
		lines.append(
			f"| `{endpoint.function}` | {endpoint.method_label} | {endpoint.access_label} | {endpoint.audience_label} |"
		)
	lines.extend(
		[
			"",
			"`Any*` means the whitelist decorator does not restrict HTTP methods; the implementation contract below remains authoritative for intended client use.",
			END,
		]
	)
	return "\n".join(lines)


def _replace_generated_block(text: str, block: str) -> str:
	if BEGIN in text or END in text:
		if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
			raise RuntimeError("Malformed generated endpoint markers")
		start = text.index(BEGIN)
		finish = text.index(END) + len(END)
		return f"{text[:start].rstrip()}\n\n{block}\n\n{text[finish:].lstrip()}".rstrip() + "\n"

	lines = text.splitlines()
	if not lines or not lines[0].startswith("# "):
		raise RuntimeError("API document must start with one H1 before endpoint inventory can be inserted")
	return "\n".join([lines[0], "", block, "", *lines[1:]]).rstrip() + "\n"


def render_reference(endpoints: list[Endpoint]) -> str:
	versioned_count = sum(endpoint.versioned for endpoint in endpoints)
	metrics_count = len(endpoints) - versioned_count
	lines = [
		"# Complete AOS API Reference",
		"",
		"> **Code-derived inventory.** Generated by `ci/validate_api_documentation.py` from the current `@frappe.whitelist` declarations. Do not hand-edit endpoint rows.",
		"",
		f"The repository currently exposes **{versioned_count} versioned v1 methods** and **{metrics_count} private metrics methods**, for **{len(endpoints)} whitelisted HTTP methods** in total.",
		"",
		"For request fields, response payloads, state rules, errors, limits and privacy semantics, follow the owning documentation link for the domain. The `Decorator access` column reports only the Frappe whitelist declaration; domain policy can impose additional authentication/authorization.",
		"",
		"`Any*` means the whitelist decorator does not restrict HTTP methods. It does not imply that every method is a supported client contract; use the owning feature documentation for intended transport semantics.",
	]

	for domain in sorted({endpoint.domain for endpoint in endpoints}, key=lambda value: DOMAIN_LABELS[value]):
		domain_endpoints = [endpoint for endpoint in endpoints if endpoint.domain == domain]
		doc_path = _doc_path(domain)
		link = _relative_link("docs/api/reference.md", doc_path)
		lines.extend(
			[
				"",
				f"## {DOMAIN_LABELS[domain]} ({len(domain_endpoints)})",
				"",
				f"Owner documentation: [{doc_path}]({link})",
				"",
				"| Route | HTTP | Decorator access | Audience | Source |",
				"|---|---|---|---|---|",
			]
		)
		for endpoint in domain_endpoints:
			lines.append(
				f"| `{endpoint.route}` | {endpoint.method_label} | {endpoint.access_label} | {endpoint.audience_label} | `{endpoint.source}` |"
			)
	return "\n".join(lines).rstrip() + "\n"


def write_docs(endpoints: list[Endpoint]) -> None:
	reference = ROOT / "docs/api/reference.md"
	reference.write_text(render_reference(endpoints), encoding="utf-8")

	for domain, relative in FEATURE_DOCS.items():
		path = ROOT / relative
		if not path.exists():
			raise RuntimeError(f"Missing feature API document: {relative}")
		block = render_inventory(domain, endpoints, relative)
		path.write_text(_replace_generated_block(path.read_text(encoding="utf-8"), block), encoding="utf-8")


def validate_docs(endpoints: list[Endpoint]) -> list[str]:
	errors: list[str] = []
	reference_path = ROOT / "docs/api/reference.md"
	if not reference_path.exists():
		errors.append("docs/api/reference.md is missing; run validate_api_documentation.py --write")
	elif reference_path.read_text(encoding="utf-8") != render_reference(endpoints):
		errors.append("docs/api/reference.md is stale; run validate_api_documentation.py --write")

	for domain, relative in FEATURE_DOCS.items():
		path = ROOT / relative
		if not path.exists():
			errors.append(f"canonical feature API document is missing: {relative}")
			continue
		text = path.read_text(encoding="utf-8")
		expected = render_inventory(domain, endpoints, relative)
		if expected not in text:
			errors.append(f"code-derived endpoint inventory is stale or missing: {relative}")

	feature_index = ROOT / "docs/features/README.md"
	if not feature_index.exists():
		errors.append("docs/features/README.md is missing")
	else:
		index_text = feature_index.read_text(encoding="utf-8")
		for relative in FEATURE_DOCS.values():
			feature_dir = Path(relative).parent.name
			if feature_dir not in index_text:
				errors.append(f"docs/features/README.md does not link feature directory: {feature_dir}")

	for domain in {endpoint.domain for endpoint in endpoints}:
		try:
			doc_path = _doc_path(domain)
		except RuntimeError as exc:
			errors.append(str(exc))
			continue
		if not (ROOT / doc_path).exists():
			errors.append(f"documentation owner for {domain} does not exist: {doc_path}")
	return errors


def main() -> int:
	parser = argparse.ArgumentParser(description="Validate code-derived AOS API documentation.")
	parser.add_argument("--write", action="store_true", help="Regenerate endpoint inventories from code.")
	args = parser.parse_args()
	endpoints = discover_endpoints()
	if args.write:
		write_docs(endpoints)
		print(f"API documentation regenerated from {len(endpoints)} whitelisted methods.")
		return 0

	errors = validate_docs(endpoints)
	if errors:
		print("API documentation validation failed:", file=sys.stderr)
		for error in errors:
			print(f"- {error}", file=sys.stderr)
		return 1
	versioned = sum(endpoint.versioned for endpoint in endpoints)
	print(f"API documentation: OK ({len(endpoints)} methods; {versioned} versioned v1)")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
