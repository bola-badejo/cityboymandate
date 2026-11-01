"""Reply templates. English is the only language used by default. The Nigerian Pidgin set below is a DRAFT
translation: a reply is only written in a language once an admin lists it in `reviewed_languages` after a fluent
reviewer has signed off the wording. Hausa, Yoruba and Igbo have no templates yet: those replies fall back to English."""
T = {
    "en": {
        "false": "Context matters here.", "misleading": "Some context is missing here.", "outdated": "This information may be out of date.",
        "supported": "This checks out.", "mostly_supported": "This is mostly accurate.", "states": "states:", "source": "Source:",
        "unverified": "We could not verify this with the sources available. We would rather say so than guess.",
        "disputed": "Credible sources disagree on this. See:",
        "opinion": "This reads as an opinion or prediction rather than a checkable fact.",
    },
    "pcm": {   # DRAFT, needs native review before use
        "false": "No be so the matter be. Check the full gist:", "misleading": "Part of the gist dey miss for here.",
        "outdated": "This info fit don old.", "supported": "This one correct.", "mostly_supported": "This one mostly correct.",
        "states": "talk say:", "source": "Source:",
        "unverified": "We no fit confirm this one with wetin we get. We go rather talk am than guess.",
        "disputed": "Serious sources no agree for this one. Look:",
        "opinion": "This one na opinion or prediction, no be fact wey person fit check.",
    },
}


def t(lang: str, key: str) -> str:
    return T.get(lang, T["en"]).get(key) or T["en"][key]
