from __future__ import annotations

from .models import SageMapping


DEFAULT_SAGE_MAPPINGS: tuple[SageMapping, ...] = (
    SageMapping("CHEMISES / TUNIQUES", "CH", "Chemise"),
    SageMapping("COMBI PANTALON", "CO", "Combi"),
    SageMapping("COMBI SHORT", "CO", "Combi"),
    SageMapping("CROCHETS", "TO", "Top / Tunic / Haut"),
    SageMapping("ENSEMBLES", "EN", "Ensemble 2pcs"),
    SageMapping("JUPES", "JU", "Jupe"),
    SageMapping("MANTEAUX / VESTES", "VE", "Veste / Manteau"),
    SageMapping("PANTALONS", "PA", "Pantalon"),
    SageMapping("PULLS / GILETS", "PU", "Pull / Gilet"),
    SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"),
    SageMapping("ROBES LONGUES", "RO", "ROBE / TUNIC"),
    SageMapping("SHORTS", "SH", "Short"),
    SageMapping("TOPS", "TO", "Top / Tunic / Haut"),
    SageMapping("VÊTEMENTS PLAGE", "RO", "ROBE / TUNIC"),
)
