from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .models import InvoiceLine, Product, normalize_spaces


MAX_PARCEL_WEIGHT_KG = Decimal("30.00")
DEFAULT_CUSTOMS_SETTINGS = {
    "parcel_content": "envoi-commercial",
    "origin_country": "Chine",
    "origin_iso": "CN",
    "hs_number": "62044300",
    "fallback_unit_weight_kg": "0.20",
    "sage_code_weights_kg": {
        "RO": "0.30",
        "PA": "0.20",
        "SH": "0.20",
    },
}


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def weight(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def decimal_from(value: object, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return default


@dataclass
class CustomsLine:
    ref: str
    description: str
    sage_code: str
    quantity: int
    unit_weight_kg: Decimal
    unit_value_ht: Decimal
    origin_country: str = "Chine"
    origin_iso: str = "CN"
    hs_number: str = "62044300"

    @property
    def total_weight_kg(self) -> Decimal:
        return weight(self.unit_weight_kg * Decimal(self.quantity))

    @property
    def total_value_ht(self) -> Decimal:
        return money(self.unit_value_ht * Decimal(self.quantity))


@dataclass
class CustomsParcel:
    name: str = "Colis 1"
    max_weight_kg: Decimal = MAX_PARCEL_WEIGHT_KG
    lines: list[CustomsLine] = field(default_factory=list)

    @property
    def total_weight_kg(self) -> Decimal:
        return weight(sum((line.total_weight_kg for line in self.lines), Decimal("0")))

    @property
    def total_value_ht(self) -> Decimal:
        return money(sum((line.total_value_ht for line in self.lines), Decimal("0")))

    def weight_errors(self) -> list[str]:
        errors: list[str] = []
        if self.max_weight_kg > MAX_PARCEL_WEIGHT_KG:
            errors.append(f"{self.name}: plafond superieur a 30 kg")
        if self.total_weight_kg > self.max_weight_kg:
            errors.append(f"{self.name}: poids declare {self.total_weight_kg} kg > plafond {self.max_weight_kg} kg")
        return errors


@dataclass
class CustomsDeclarationDraft:
    source: str
    order_key: str
    parcel_content: str = "envoi-commercial"
    parcels: list[CustomsParcel] = field(default_factory=list)

    @property
    def total_weight_kg(self) -> Decimal:
        return weight(sum((parcel.total_weight_kg for parcel in self.parcels), Decimal("0")))

    @property
    def total_value_ht(self) -> Decimal:
        return money(sum((parcel.total_value_ht for parcel in self.parcels), Decimal("0")))

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        for parcel in self.parcels:
            errors.extend(parcel.weight_errors())
        return errors


def customs_settings(settings: dict | None) -> dict:
    merged = json.loads(json.dumps(DEFAULT_CUSTOMS_SETTINGS))
    if isinstance(settings, dict):
        for key, value in settings.items():
            if key == "sage_code_weights_kg" and isinstance(value, dict):
                merged[key].update({str(k).upper(): str(v) for k, v in value.items()})
            else:
                merged[key] = value
    return merged


def unit_weight_for_line(line: InvoiceLine, product: Product | None, settings: dict | None = None) -> Decimal:
    options = customs_settings(settings)
    if product and product.weight_grams:
        return weight(Decimal(product.weight_grams) / Decimal("1000"))
    weights = options.get("sage_code_weights_kg") if isinstance(options.get("sage_code_weights_kg"), dict) else {}
    by_code = weights.get((line.sage_code or "").strip().upper())
    return weight(decimal_from(by_code, decimal_from(options.get("fallback_unit_weight_kg"), Decimal("0.20"))))


def customs_line_from_invoice_line(line: InvoiceLine, product: Product | None, settings: dict | None = None) -> CustomsLine:
    options = customs_settings(settings)
    origin_country = normalize_spaces(product.origin_country if product and product.origin_country else str(options.get("origin_country") or "Chine"))
    origin_iso = str(options.get("origin_iso") or "CN")
    return CustomsLine(
        ref=line.ref,
        description=normalize_spaces(line.description or line.ref),
        sage_code=line.sage_code,
        quantity=max(0, int(line.quantity_pieces or 0)),
        unit_weight_kg=unit_weight_for_line(line, product, options),
        unit_value_ht=money(line.unit_price_ht or Decimal("0")),
        origin_country=origin_country or "Chine",
        origin_iso=origin_iso,
        hs_number=str(options.get("hs_number") or "62044300").strip(),
    )


def build_customs_draft(
    source: str,
    order_key: str,
    lines: list[InvoiceLine],
    products_by_ref: dict[str, Product],
    settings: dict | None = None,
) -> CustomsDeclarationDraft:
    options = customs_settings(settings)
    customs_lines = [
        customs_line_from_invoice_line(line, products_by_ref.get(line.ref.strip().upper()), options)
        for line in lines
        if line.quantity_pieces > 0
    ]
    return CustomsDeclarationDraft(
        source=source,
        order_key=order_key,
        parcel_content=str(options.get("parcel_content") or "envoi-commercial"),
        parcels=[CustomsParcel(name="Colis 1", lines=customs_lines)],
    )


def draft_to_payload(draft: CustomsDeclarationDraft) -> dict:
    return {
        "source": draft.source,
        "order_key": draft.order_key,
        "parcel_content": draft.parcel_content,
        "parcels": [
            {
                "name": parcel.name,
                "max_weight_kg": str(parcel.max_weight_kg),
                "lines": [
                    {
                        "ref": line.ref,
                        "description": line.description,
                        "sage_code": line.sage_code,
                        "quantity": line.quantity,
                        "unit_weight_kg": str(line.unit_weight_kg),
                        "unit_value_ht": str(line.unit_value_ht),
                        "origin_country": line.origin_country,
                        "origin_iso": line.origin_iso,
                        "hs_number": line.hs_number,
                    }
                    for line in parcel.lines
                ],
            }
            for parcel in draft.parcels
        ],
    }


def draft_from_payload(payload: dict) -> CustomsDeclarationDraft | None:
    if not isinstance(payload, dict):
        return None
    parcels: list[CustomsParcel] = []
    for index, raw_parcel in enumerate(payload.get("parcels") or [], start=1):
        if not isinstance(raw_parcel, dict):
            continue
        lines: list[CustomsLine] = []
        for raw_line in raw_parcel.get("lines") or []:
            if not isinstance(raw_line, dict):
                continue
            lines.append(
                CustomsLine(
                    ref=str(raw_line.get("ref") or ""),
                    description=str(raw_line.get("description") or ""),
                    sage_code=str(raw_line.get("sage_code") or ""),
                    quantity=int(raw_line.get("quantity") or 0),
                    unit_weight_kg=decimal_from(raw_line.get("unit_weight_kg")),
                    unit_value_ht=decimal_from(raw_line.get("unit_value_ht")),
                    origin_country=str(raw_line.get("origin_country") or "Chine"),
                    origin_iso=str(raw_line.get("origin_iso") or "CN"),
                    hs_number=str(raw_line.get("hs_number") or "62044300"),
                )
            )
        parcels.append(
            CustomsParcel(
                name=str(raw_parcel.get("name") or f"Colis {index}"),
                max_weight_kg=min(decimal_from(raw_parcel.get("max_weight_kg"), MAX_PARCEL_WEIGHT_KG), MAX_PARCEL_WEIGHT_KG),
                lines=lines,
            )
        )
    return CustomsDeclarationDraft(
        source=str(payload.get("source") or ""),
        order_key=str(payload.get("order_key") or ""),
        parcel_content=str(payload.get("parcel_content") or "envoi-commercial"),
        parcels=parcels or [CustomsParcel()],
    )


def build_laposte_script(parcel: CustomsParcel, parcel_content: str = "envoi-commercial") -> str:
    items = [
        {
            "description": line.description,
            "originIso": line.origin_iso or "CN",
            "hs": line.hs_number,
            "unitWeight": format(line.unit_weight_kg, "f"),
            "unitValue": format(line.unit_value_ht, "f"),
            "quantity": line.quantity,
        }
        for line in parcel.lines
        if line.quantity > 0
    ]
    data = json.dumps({"parcelContent": parcel_content, "items": items}, ensure_ascii=False)
    return f"""(() => {{
  const declaration = {data};
  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const byName = (name) => document.querySelector(`[name="${{name}}"]`);
  const setValue = (el, value) => {{
    if (!el) throw new Error("Champ introuvable");
    el.value = String(value);
    el.dispatchEvent(new Event("input", {{ bubbles: true }}));
    el.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }};
  const clickButton = (text) => {{
    const button = [...document.querySelectorAll("button")].find((item) => (item.textContent || "").includes(text));
    if (!button) throw new Error(`Bouton introuvable: ${{text}}`);
    button.click();
  }};
  const fillOne = async (item, index) => {{
    if (index === 0) clickButton("Déclarer un objet");
    else clickButton("Ajouter un article");
    await wait(250);
    setValue(byName("description"), item.description);
    setValue(byName("originIso"), item.originIso);
    setValue(byName("SHNumber"), item.hs);
    setValue(byName("unitWeight"), item.unitWeight);
    setValue(byName("unitValue"), item.unitValue);
    const qty = document.querySelector('input[type="number"]');
    setValue(qty, item.quantity);
    await wait(100);
    clickButton("Enregistrer cet objet");
    await wait(350);
  }};
  (async () => {{
    if (!document.querySelector('[role="dialog"]')) clickButton("Commencer votre déclaration");
    await wait(250);
    setValue(byName("parcelContent"), declaration.parcelContent);
    for (let index = 0; index < declaration.items.length; index += 1) {{
      await fillOne(declaration.items[index], index);
    }}
    console.log("Déclaration La Poste remplie. Vérifiez les totaux avant l'étape suivante.");
  }})().catch((error) => alert(`Assistant douane: ${{error.message}}`));
}})();"""
