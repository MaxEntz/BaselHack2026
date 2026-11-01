"""How text is compared when accents must not matter: one definition (#220).

A guest's name (`agent.guests`), a search (`agent.discovery`) and a crawled link (`builder.urls`)
all ignore accents the same way.
"""

import unicodedata


def strip_accents(text: str) -> str:
    """`text` without its accents: "Réserver" is "Reserver", "Müller" is "Muller".

    NFKD splits a letter from its accents, which are then dropped; it also spells out
    compatibility characters ("ﬁ" is "fi"). Letters NFKD keeps whole ("œ", "ø") stay.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
