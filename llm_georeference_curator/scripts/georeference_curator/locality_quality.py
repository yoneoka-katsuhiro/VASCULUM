from __future__ import annotations

import re


COUNTRY_ONLY = {
    "china",
    "japan",
    "taiwan",
    "chinese taipei",
    "philippines",
    "nepal",
    "india",
    "vietnam",
    "thailand",
    "malaysia",
    "indonesia",
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def useful_locality_text(row, label) -> str:
    label_text = "" if label.label_source == "dwc_text" else label.label_transcription
    return " ".join(
        part
        for part in [
            row.get("verbatimLocality", ""),
            row.get("locality", ""),
            row.get("county", ""),
            row.get("municipality", ""),
            label.locality_text,
            label_text,
        ]
        if part
    )


def insufficient_locality_reason(row, label) -> str:
    text = normalize(useful_locality_text(row, label))
    country = normalize(row.get("country", ""))
    state = normalize(row.get("stateProvince", ""))

    if not text:
        if country or state:
            return "Only country/state-level locality is available."
        return "No locality text is available."

    stripped = text.strip(" .,:;")
    if stripped in COUNTRY_ONLY or stripped == country or stripped == state:
        return f"Locality is too broad for georeferencing: {stripped}."

    tokens = re.findall(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff0-9]+", text)
    informative = [token for token in tokens if token not in {"pref", "province", "county", "country"}]
    admin_tokens = set()
    admin_tokens.update(re.findall(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff0-9]+", country))
    admin_tokens.update(re.findall(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff0-9]+", state))
    if informative and admin_tokens and set(informative).issubset(admin_tokens):
        return f"Locality is too broad for georeferencing: {stripped}."
    if len(informative) <= 1 and stripped in {country, state}:
        return f"Locality is too broad for georeferencing: {stripped}."

    broad_admin = any(term in text for term in ["prefecture", "province", "pref."])
    has_detail = any(
        term in text
        for term in [
            "mt.",
            "mount",
            "mountain",
            "river",
            "stream",
            "valley",
            "trail",
            "village",
            "mura",
            "gun",
            "county",
            "forest",
            "road",
            "km",
        ]
    )
    if broad_admin and not has_detail and len(informative) <= 2:
        return "Only broad administrative locality is available."

    return ""
