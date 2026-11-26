# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Deterministic stdlib HTML form DOM extractor for adversarial matcher tests.

Converts static HTML form fixtures (including nested labels, ARIA references,
fieldsets/legends, and declarative Shadow DOM templates) into the value-free
field metadata dicts consumed by ``agent_autofill.form_memory.match_form_fields``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

_SKIP_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}
_SPACE = re.compile(r"\s+")
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def _clean_text(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _detect_section(text: str) -> str | None:
    lowered = text.lower()
    has_shipping = any(tok in lowered for tok in ("shipping", "delivery", "ship to"))
    has_billing = any(tok in lowered for tok in ("billing", "bill to"))
    if has_shipping and not has_billing:
        return "shipping"
    if has_billing and not has_shipping:
        return "billing"
    return None


class _FormDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[dict[str, Any]] = []
        self._id_texts: dict[str, list[str]] = {}
        self._label_for_texts: dict[str, list[str]] = {}
        self._controls: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v if v is not None else "") for k, v in attrs}
        node: dict[str, Any] = {
            "tag": tag,
            "attrs": attr_map,
            "texts": [],
            "legend": None,
            "controls": [],
        }
        self._stack.append(node)

        if tag in {"input", "select", "textarea"}:
            input_type = attr_map.get("type", "text" if tag == "input" else tag).strip().lower()
            if tag == "input" and input_type in _SKIP_INPUT_TYPES:
                if tag in _VOID_TAGS:
                    self._stack.pop()
                return
            control = self._build_control(tag, input_type, attr_map)
            self._controls.append(control)
            for anc in self._stack[:-1]:
                if anc["tag"] == "label":
                    anc["controls"].append(control)

        if tag in _VOID_TAGS:
            self._finish_node(self._stack.pop())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS and self._stack and self._stack[-1]["tag"] == tag.lower():
            self._finish_node(self._stack.pop())

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx]["tag"] == tag:
                while len(self._stack) > idx:
                    self._finish_node(self._stack.pop())
                break

    def handle_data(self, data: str) -> None:
        if not data or not self._stack:
            return
        # Ignore <option> text inside <select> so option values never pollute label text.
        if any(node["tag"] == "option" for node in self._stack):
            return
        # Ignore aria-hidden="true" decorative spans when assembling visible text?
        # Keep required markers like "*" so normalizer tests exercise them too.
        for node in self._stack:
            node["texts"].append(data)

    def _finish_node(self, node: dict[str, Any]) -> None:
        tag = node["tag"]
        attrs = node["attrs"]
        text = _clean_text(" ".join(node["texts"]))
        elem_id = attrs.get("id", "").strip()
        if elem_id and text and tag not in {"input", "select", "textarea", "form", "fieldset"}:
            self._id_texts.setdefault(elem_id, []).append(text)
        if tag == "legend" and text:
            for anc in reversed(self._stack):
                if anc["tag"] == "fieldset" and anc["legend"] is None:
                    anc["legend"] = text
                    break
        if tag == "label":
            target_for = attrs.get("for", "").strip()
            if target_for and text:
                self._label_for_texts.setdefault(target_for, []).append(text)
            if node["controls"] and text:
                for control in node["controls"]:
                    if not control.get("_wrapped_label"):
                        control["_wrapped_label"] = text

    def _build_control(
        self, tag: str, input_type: str, attrs: dict[str, str]
    ) -> dict[str, Any]:
        legends: list[str] = []
        section: str | None = None
        for anc in self._stack[:-1]:
            if anc["tag"] == "fieldset" and anc.get("legend"):
                legends.append(anc["legend"])
                detected = _detect_section(anc["legend"])
                if detected:
                    section = detected
            else:
                anc_hint = " ".join(
                    attrs_val
                    for key in ("id", "class", "data-section", "aria-label")
                    if (attrs_val := anc["attrs"].get(key, ""))
                )
                if anc_hint:
                    detected = _detect_section(anc_hint)
                    if detected and section is None:
                        section = detected

        control: dict[str, Any] = {
            "field_id": attrs.get("id") or attrs.get("name") or "",
            "tag": tag,
            "type": input_type,
            "name": attrs.get("name", ""),
            "autocomplete": attrs.get("autocomplete", ""),
            "role": attrs.get("role", ""),
            "aria_label": attrs.get("aria-label", ""),
            "aria_labelledby": attrs.get("aria-labelledby", ""),
            "placeholder": attrs.get("placeholder", ""),
            "required": "required" in attrs or attrs.get("aria-required", "").lower() == "true",
            "maxlength": attrs.get("maxlength", ""),
            "inputmode": attrs.get("inputmode", ""),
            "legends": legends,
            "section": section,
            "_wrapped_label": "",
        }
        return control

    def extract_fields(self) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        for ctrl in self._controls:
            fid = ctrl["field_id"]
            label_parts: list[str] = []
            if fid and fid in self._label_for_texts:
                label_parts.append(" ".join(self._label_for_texts[fid]))
            elif ctrl["_wrapped_label"]:
                label_parts.append(ctrl["_wrapped_label"])

            labelledby_text = ""
            if ctrl["aria_labelledby"]:
                ref_parts = [
                    " ".join(self._id_texts[ref_id])
                    for ref_id in ctrl["aria_labelledby"].split()
                    if ref_id in self._id_texts
                ]
                labelledby_text = _clean_text(" ".join(ref_parts))

            aria_label = _clean_text(ctrl["aria_label"])
            explicit_label = _clean_text(" ".join(label_parts))
            placeholder = _clean_text(ctrl["placeholder"])
            legend_text = _clean_text(" ".join(ctrl["legends"]))

            # Accessible name priority: aria-labelledby -> aria-label -> <label> -> placeholder -> legend -> name/id
            primary_label = (
                labelledby_text
                or aria_label
                or explicit_label
                or placeholder
                or legend_text
                or ctrl["name"]
                or fid
            )

            field: dict[str, Any] = {
                "field_id": fid,
                "label": primary_label,
                "required": ctrl["required"],
            }
            for key in ("name", "autocomplete", "type", "role", "placeholder", "inputmode"):
                if ctrl[key]:
                    field[key] = ctrl[key]
            if aria_label:
                field["aria_label"] = aria_label
            if ctrl["maxlength"].isdigit():
                field["maxlength"] = int(ctrl["maxlength"])
            if ctrl["section"]:
                field["section"] = ctrl["section"]
            if legend_text:
                field["group_legend"] = legend_text
            fields.append(field)
        return fields


def extract_form_fields_from_html(html: str) -> list[dict[str, Any]]:
    """Parse an HTML form DOM snippet into value-free field metadata dicts."""

    parser = _FormDOMParser()
    parser.feed(html)
    parser.close()
    return parser.extract_fields()
