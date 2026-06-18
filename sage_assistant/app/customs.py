from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook

from .models import InvoiceLine, Product, normalize_spaces


MAX_PARCEL_WEIGHT_KG = Decimal("30.00")
CUSTOMS_WEIGHT_SAFETY_MARGIN_KG = Decimal("0.010")
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
        "EN": "0.50",
        "CO": "0.40",
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
    package_size: int | None = None

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

    def renumber_parcels(self) -> None:
        for index, parcel in enumerate(self.parcels, start=1):
            parcel.name = f"Colis {index}"

    def remove_parcel(self, index: int) -> bool:
        if len(self.parcels) <= 1 or index < 0 or index >= len(self.parcels):
            return False
        del self.parcels[index]
        self.renumber_parcels()
        return True


def customs_line_key(line: CustomsLine) -> str:
    return (line.ref or line.description or "").strip().upper()


def clone_customs_line(line: CustomsLine, quantity: int | None = None) -> CustomsLine:
    return CustomsLine(
        ref=line.ref,
        description=line.description,
        sage_code=line.sage_code,
        quantity=max(0, int(line.quantity if quantity is None else quantity)),
        unit_weight_kg=line.unit_weight_kg,
        unit_value_ht=line.unit_value_ht,
        origin_country=line.origin_country,
        origin_iso=line.origin_iso,
        hs_number=line.hs_number,
        package_size=line.package_size,
    )


def customs_line_templates(draft: CustomsDeclarationDraft) -> dict[str, CustomsLine]:
    templates: dict[str, CustomsLine] = {}
    for parcel in draft.parcels:
        for line in parcel.lines:
            key = customs_line_key(line)
            if key and key not in templates:
                templates[key] = clone_customs_line(line, 0)
    return templates


def expected_quantities_by_ref(source_draft: CustomsDeclarationDraft) -> dict[str, int]:
    quantities: dict[str, int] = {}
    if not source_draft.parcels:
        return quantities
    for line in source_draft.parcels[0].lines:
        key = customs_line_key(line)
        if key:
            quantities[key] = quantities.get(key, 0) + max(0, int(line.quantity))
    return quantities


def allocated_quantities_by_ref(draft: CustomsDeclarationDraft) -> dict[str, int]:
    quantities: dict[str, int] = {}
    for parcel in draft.parcels:
        for line in parcel.lines:
            key = customs_line_key(line)
            if key:
                quantities[key] = quantities.get(key, 0) + max(0, int(line.quantity))
    return quantities


def remaining_quantities_by_ref(draft: CustomsDeclarationDraft, source_draft: CustomsDeclarationDraft) -> dict[str, int]:
    expected = expected_quantities_by_ref(source_draft)
    allocated = allocated_quantities_by_ref(draft)
    return {key: max(0, quantity - allocated.get(key, 0)) for key, quantity in expected.items()}


def sync_parcel_templates(draft: CustomsDeclarationDraft, source_draft: CustomsDeclarationDraft) -> None:
    source_templates = customs_line_templates(source_draft)
    source_keys = list(source_templates.keys())
    if not draft.parcels:
        draft.parcels.append(CustomsParcel(name="Colis 1"))
    for parcel in draft.parcels:
        by_key = {customs_line_key(line): line for line in parcel.lines if customs_line_key(line)}
        synced: list[CustomsLine] = []
        for key in source_keys:
            existing = by_key.get(key)
            if existing is None:
                synced.append(clone_customs_line(source_templates[key], 0))
            else:
                refreshed = clone_customs_line(source_templates[key], existing.quantity)
                refreshed.unit_weight_kg = existing.unit_weight_kg
                refreshed.unit_value_ht = existing.unit_value_ht
                refreshed.origin_country = existing.origin_country
                refreshed.origin_iso = existing.origin_iso
                refreshed.hs_number = existing.hs_number
                synced.append(refreshed)
        parcel.lines = synced
    draft.renumber_parcels()


def set_draft_parcel_count(draft: CustomsDeclarationDraft, source_draft: CustomsDeclarationDraft, count: int) -> None:
    sync_parcel_templates(draft, source_draft)
    count = max(1, int(count))
    templates = list(customs_line_templates(source_draft).values())
    while len(draft.parcels) < count:
        draft.parcels.append(
            CustomsParcel(
                name=f"Colis {len(draft.parcels) + 1}",
                lines=[clone_customs_line(line, 0) for line in templates],
            )
        )
    while len(draft.parcels) > count:
        draft.parcels.pop()
    draft.renumber_parcels()


def add_quantity_to_parcel(draft: CustomsDeclarationDraft, source_draft: CustomsDeclarationDraft, parcel_index: int, line_key: str, quantity: int) -> int:
    sync_parcel_templates(draft, source_draft)
    if parcel_index < 0 or parcel_index >= len(draft.parcels):
        return 0
    requested = max(0, int(quantity))
    if requested <= 0:
        return 0
    key = line_key.strip().upper()
    remaining = remaining_quantities_by_ref(draft, source_draft).get(key, 0)
    to_add = min(requested, remaining)
    if to_add <= 0:
        return 0
    for line in draft.parcels[parcel_index].lines:
        if customs_line_key(line) == key:
            line.quantity += to_add
            return to_add
    return 0


def remove_quantity_from_parcel(draft: CustomsDeclarationDraft, parcel_index: int, line_key: str, quantity: int | None = None) -> int:
    if parcel_index < 0 or parcel_index >= len(draft.parcels):
        return 0
    key = line_key.strip().upper()
    for line in draft.parcels[parcel_index].lines:
        if customs_line_key(line) != key:
            continue
        removable = line.quantity if quantity is None else min(line.quantity, max(0, int(quantity)))
        line.quantity -= removable
        return removable
    return 0


def set_parcel_line_quantity(draft: CustomsDeclarationDraft, source_draft: CustomsDeclarationDraft, parcel_index: int, line_key: str, target_quantity: int) -> int:
    sync_parcel_templates(draft, source_draft)
    if parcel_index < 0 or parcel_index >= len(draft.parcels):
        return 0
    key = line_key.strip().upper()
    parcel = draft.parcels[parcel_index]
    current_line = next((line for line in parcel.lines if customs_line_key(line) == key), None)
    if current_line is None:
        return 0
    current_quantity = current_line.quantity
    available = current_quantity + remaining_quantities_by_ref(draft, source_draft).get(key, 0)
    target = max(0, min(int(target_quantity), available))
    current_line.quantity = target
    return target


def packing_list_text(draft: CustomsDeclarationDraft) -> str:
    lines: list[str] = []
    for parcel in draft.parcels:
        refs = [f"{line.ref} x{line.quantity}" for line in parcel.lines if line.quantity > 0]
        if refs:
            lines.append(f"{parcel.name}: {', '.join(refs)}")
    return "\n".join(lines)


def export_packing_list_xlsx(draft: CustomsDeclarationDraft, path: str | Path, exported_at: datetime | None = None) -> None:
    exported_at = exported_at or datetime.now()
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Résumé"
    summary.append(["Source", draft.source])
    summary.append(["Facture / commande", draft.order_key])
    summary.append(["Nombre de colis", len(draft.parcels)])
    summary.append(["Poids total kg", float(draft.total_weight_kg)])
    summary.append(["Valeur totale HT", float(draft.total_value_ht)])
    summary.append(["Date export", exported_at.strftime("%Y-%m-%d %H:%M")])

    sheet = workbook.create_sheet("Colisage")
    sheet.append(
        [
            "Colis",
            "Ref",
            "Description",
            "Quantité",
            "Poids unitaire kg",
            "Poids total kg",
            "Valeur unitaire HT",
            "Valeur totale HT",
        ]
    )
    for parcel in draft.parcels:
        for line in parcel.lines:
            if line.quantity <= 0:
                continue
            sheet.append(
                [
                    parcel.name,
                    line.ref,
                    line.description,
                    line.quantity,
                    float(line.unit_weight_kg),
                    float(line.total_weight_kg),
                    float(line.unit_value_ht),
                    float(line.total_value_ht),
                ]
            )
        sheet.append([parcel.name, "TOTAL", "", "", "", float(parcel.total_weight_kg), "", float(parcel.total_value_ht)])

    for worksheet in (summary, sheet):
        for column_cells in worksheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 42)

    workbook.save(path)


def adapt_parcel_weights(parcel: CustomsParcel, target_weight_kg: Decimal) -> None:
    positive_lines = [line for line in parcel.lines if line.quantity > 0 and line.unit_weight_kg > 0]
    current_weight = parcel.total_weight_kg
    if not positive_lines or current_weight <= 0 or target_weight_kg <= 0:
        return
    safe_target = max(Decimal("0.001"), target_weight_kg - CUSTOMS_WEIGHT_SAFETY_MARGIN_KG)
    factor = safe_target / current_weight
    for line in positive_lines:
        line.unit_weight_kg = weight(line.unit_weight_kg * factor)
    while parcel.total_weight_kg > target_weight_kg:
        adjusted = False
        for line in sorted(positive_lines, key=lambda item: item.quantity, reverse=True):
            if line.unit_weight_kg <= Decimal("0.001"):
                continue
            line.unit_weight_kg = weight(line.unit_weight_kg - Decimal("0.001"))
            adjusted = True
            if parcel.total_weight_kg <= target_weight_kg:
                return
        if not adjusted:
            return


def parcel_to_laposte_payload(parcel: CustomsParcel, parcel_content: str = "envoi-commercial") -> dict:
    return {
        "parcelContent": parcel_content,
        "parcelName": parcel.name,
        "maxWeightKg": str(parcel.max_weight_kg),
        "totalWeightKg": str(parcel.total_weight_kg),
        "totalValueHt": str(parcel.total_value_ht),
        "items": [
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
        ],
    }


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
        package_size=line.package_size,
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
                        "package_size": line.package_size,
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
                    package_size=int(raw_line["package_size"]) if raw_line.get("package_size") else None,
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
    data = json.dumps(parcel_to_laposte_payload(parcel, parcel_content), ensure_ascii=False)
    return f"""(() => {{
  const declaration = {data};
  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const norm = (text) => (text || "").normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
  const visible = (el) => !!(el && el.offsetParent !== null && !el.disabled);
  const byName = (name) => [...document.querySelectorAll(`[name="${{name}}"]`)].reverse().find(visible);
  const waitForName = async (name) => {{
    for (let attempt = 0; attempt < 20; attempt += 1) {{
      const el = byName(name);
      if (el) return el;
      await wait(100);
    }}
    throw new Error(`Champ introuvable: ${{name}}`);
  }};
  const setValue = (el, value) => {{
    if (!el) throw new Error("Champ introuvable");
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value")?.set;
    if (setter) setter.call(el, String(value));
    else el.value = String(value);
    el.dispatchEvent(new InputEvent("input", {{ bubbles: true, inputType: "insertText", data: String(value) }}));
    el.dispatchEvent(new Event("change", {{ bubbles: true }}));
    el.blur();
  }};
  const clickButton = (text) => {{
    const wanted = norm(text);
    const button = [...document.querySelectorAll("button")].find((item) => visible(item) && norm(item.textContent).includes(wanted));
    if (!button) throw new Error(`Bouton introuvable: ${{text}}`);
    button.click();
  }};
  const fillOne = async (item, index) => {{
    if (index === 0) clickButton("Declarer un objet");
    else clickButton("Ajouter un article");
    await waitForName("description");
    setValue(byName("description"), item.description);
    setValue(await waitForName("originIso"), item.originIso);
    setValue(await waitForName("SHNumber"), item.hs);
    setValue(await waitForName("unitWeight"), item.unitWeight);
    setValue(await waitForName("unitValue"), item.unitValue);
    const qty = [...document.querySelectorAll('input[type="number"]')].reverse().find(visible);
    setValue(qty, item.quantity);
    await wait(100);
    clickButton("Enregistrer cet objet");
    await wait(350);
  }};
  (async () => {{
    if (!document.querySelector('[role="dialog"]')) clickButton("Commencer votre declaration");
    await wait(250);
    setValue(await waitForName("parcelContent"), declaration.parcelContent);
    for (let index = 0; index < declaration.items.length; index += 1) {{
      await fillOne(declaration.items[index], index);
    }}
    console.log("Declaration La Poste remplie. Verifiez les totaux avant l'etape suivante.");
  }})().catch((error) => alert(`Assistant douane: ${{error.message}}`));
}})();"""
