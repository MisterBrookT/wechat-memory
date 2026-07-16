from __future__ import annotations

import xml.etree.ElementTree as ET


def readable_content(content: str, message_type: str) -> str:
    """Return human-readable text without exposing credentials embedded in WeChat XML."""
    raw = str(content or "").strip()
    kind = str(message_type or "unknown")
    if not raw:
        return "" if kind == "文本" else f"[{kind}]"
    positions = [position for marker in ("<?xml", "<msg", "<appmsg") if (position := raw.find(marker)) >= 0]
    if not positions:
        if any(marker in raw.lower() for marker in ("<aeskey", "<fileuploadtoken", "<cdnattachurl", "<attachid")):
            return f"[{kind}]"
        return raw[:4000]
    raw = raw[min(positions) :]
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return f"[{kind}]"
    values: list[str] = []
    for path in (".//appmsg/title", ".//appmsg/des", ".//location/@label"):
        if "/@" in path:
            parent_path, attribute = path.split("/@", 1)
            node = root.find(parent_path)
            value = node.attrib.get(attribute, "") if node is not None else ""
        else:
            node = root.find(path)
            value = node.text if node is not None else ""
        value = str(value or "").strip()
        if value and not value.startswith("<") and value not in values:
            values.append(value)
    return " · ".join(values)[:4000] if values else f"[{kind}]"
