from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import requests
import yaml

from .errors import NetBoxChatError
from .models import NDX_COMPONENT_ENDPOINTS, NDX_OBJECT_CONFIG, netbox_slug


class NDXConnector:
    INDEX_URL = "https://netboxlabs.com/ndx/data/search-index.json"
    VENDORS_URL = "https://netboxlabs.com/ndx/data/vendors.json"
    LIBRARY_REPO_API = "https://api.github.com/repos/netbox-community/devicetype-library"
    COMPONENT_KEYS = tuple(NDX_COMPONENT_ENDPOINTS)
    PARENT_FIELDS = (
        "model", "slug", "part_number", "u_height", "airflow", "weight", "weight_unit",
        "description", "comments", "attributes", "profile", "exclude_from_utilization",
        "is_full_depth", "subdevice_role", "front_image", "rear_image",
    )

    def __init__(self, session: Any = requests):
        self.session = session
        self._records: list[dict[str, Any]] | None = None
        self._vendors: list[dict[str, Any]] | None = None

    @staticmethod
    def normalize(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

    @classmethod
    def tokens(cls, value: Any) -> list[str]:
        return re.findall(r"[a-z0-9]+", str(value or "").casefold())

    @classmethod
    def acronym(cls, value: Any) -> str:
        return "".join(token[0] for token in cls.tokens(value) if token)

    def _json(self, url: str, timeout: int = 30) -> Any:
        response = self.session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def records(self) -> list[dict[str, Any]]:
        if self._records is None:
            data = self._json(self.INDEX_URL)
            self._records = [item for item in data if isinstance(item, dict)]
        return list(self._records)

    def vendors(self) -> list[dict[str, Any]]:
        if self._vendors is None:
            data = self._json(self.VENDORS_URL)
            self._vendors = [item for item in data if isinstance(item, dict)]
        return list(self._vendors)

    def resolve_vendor(self, value: Any) -> dict[str, Any] | None:
        query = self.normalize(value)
        if not query:
            return None
        ranked: list[tuple[int, dict[str, Any]]] = []
        for vendor in self.vendors():
            labels = [vendor.get("display_name"), vendor.get("name"), vendor.get("display"), vendor.get("slug")]
            labels = [label for label in labels if label]
            scores = []
            for label in labels:
                normalized = self.normalize(label)
                token_values = [self.normalize(token) for token in self.tokens(label)]
                if query == normalized:
                    scores.append(100)
                elif query == self.acronym(label):
                    scores.append(90)
                elif query in token_values:
                    scores.append(80)
                elif query in normalized or normalized in query:
                    scores.append(60)
            if scores:
                ranked.append((max(scores), vendor))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("name") or item[1].get("slug") or "")))
        if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
            return None
        return ranked[0][1]

    def search(self, query: Any) -> list[dict[str, Any]]:
        text = self.normalize(query)
        if not text:
            raise NetBoxChatError("NDX exige query, model ou part_number.")
        vendor = self.resolve_vendor(query)
        vendor_values = {self.normalize(vendor.get(key)) for key in ("display_name", "name", "display", "slug") if vendor and vendor.get(key)}
        output = []
        for item in self.records():
            values = {self.normalize(value) for value in self._scalars(item) if value not in (None, "")}
            if any(text in value for value in values) or bool(values & vendor_values):
                output.append(item)
        return output

    @classmethod
    def _scalars(cls, value: Any):
        if isinstance(value, dict):
            for item in value.values():
                yield from cls._scalars(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from cls._scalars(item)
        elif isinstance(value, (str, int, float, bool)):
            yield value

    def exact(self, query: Any, candidates: list[dict[str, Any]], object_type: str = "device-type") -> dict[str, Any] | None:
        wanted = self.normalize(query)
        matches = [item for item in candidates if item.get("type") == object_type and wanted in {self.normalize(item.get("model")), self.normalize(item.get("part_number")), self.normalize(item.get("slug"))}]
        return matches[0] if len(matches) == 1 else None

    def _default_branch(self) -> str:
        metadata = self._json(self.LIBRARY_REPO_API, timeout=15)
        branch = str(metadata.get("default_branch") or "").strip() if isinstance(metadata, dict) else ""
        if not branch:
            raise NetBoxChatError("La source de specs NDX ne publie aucune branche par défaut.")
        return branch

    def load_spec(self, item: dict[str, Any], object_type: str) -> dict[str, Any]:
        components = item.get("component_templates")
        if isinstance(components, dict) and any(isinstance(values, list) and values for values in components.values()):
            return {**item, **components}
        source = str(item.get("source") or "").casefold()
        if source not in {"community", "both"}:
            return {}
        vendor = str(item.get("manufacturer") or "").strip()
        if not vendor:
            return {}
        config = NDX_OBJECT_CONFIG[object_type]
        branch = self._default_branch()
        listing_url = f"{self.LIBRARY_REPO_API}/contents/{config['directory']}/{quote(vendor, safe='')}?ref={quote(branch, safe='')}"
        listing = self._json(listing_url, timeout=15)
        identifiers = {
            self.normalize(item.get("model")),
            self.normalize(item.get("part_number")),
            self.normalize(item.get("slug")),
        } - {""}
        matches = []
        for entry in listing if isinstance(listing, list) else []:
            name = str(entry.get("name") or "")
            stem = name[:-5] if name.casefold().endswith(".yaml") else ""
            if stem and self.normalize(stem) in identifiers:
                matches.append(entry)
        if len(matches) != 1:
            return {}
        url = str(matches[0].get("download_url") or "")
        if not url:
            return {}
        response = self.session.get(url, timeout=15)
        if response.status_code != 200:
            return {}
        raw = yaml.safe_load(response.text) or {}
        if not isinstance(raw, dict):
            return {}
        return raw

    def build_payload(self, query: Any, object_type: str = "device-type") -> dict[str, Any] | None:
        if object_type not in NDX_OBJECT_CONFIG:
            raise NetBoxChatError(f"Type NDX non supporté : {object_type}")
        candidates = self.search(query)
        item = self.exact(query, candidates, object_type=object_type)
        if item is None:
            return None
        raw = self.load_spec(item, object_type)
        vendor = self.resolve_vendor(item.get("manufacturer") or item.get("vendor_name"))
        manufacturer = str(raw.get("manufacturer") or item.get("manufacturer") or (vendor or {}).get("display_name") or item.get("vendor_name") or "")
        merged = {**item, **raw}
        parent = {key: merged.get(key) for key in self.PARENT_FIELDS if merged.get(key) is not None}
        parent["model"] = parent.get("model") or item.get("model") or item.get("part_number")
        parent["slug"] = netbox_slug(parent.get("slug") or parent["model"])
        if object_type == "device-type":
            parent["u_height"] = parent.get("u_height") or 1
        else:
            parent.pop("u_height", None)
            parent.pop("front_image", None)
            parent.pop("rear_image", None)
        components = {key: raw.get(key, []) for key in self.COMPONENT_KEYS if isinstance(raw.get(key), list)}
        return {
            "object_type": object_type,
            "manufacturer": manufacturer,
            "parent": parent,
            "component_templates": components,
        }
