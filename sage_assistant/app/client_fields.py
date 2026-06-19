from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .portal_orders import PortalClient


@dataclass(frozen=True)
class ClientColumnDefinition:
    key: str
    label: str
    aliases: tuple[str, ...] = ()
    width: int = 140
    default_visible: bool = True
    section: str = "Divers"


CLIENT_COLUMN_DEFINITIONS: tuple[ClientColumnDefinition, ...] = (
    ClientColumnDefinition("name", "Nom", ("name", "customer_name"), 180, True, "Identité"),
    ClientColumnDefinition("classification", "Classification des clients", ("vip", "classification"), 150, True, "Identité"),
    ClientColumnDefinition("discount", "Remise", ("discount", "remise"), 90, True, "Facturation"),
    ClientColumnDefinition("debt", "Total dû", ("debt", "currency_debt", "total_due"), 100, True, "Facturation"),
    ClientColumnDefinition("zone", "Zone", ("zone", "area"), 100, True, "Divers"),
    ClientColumnDefinition("custom_1", "To define 1", ("define_1", "to_define_1", "custom_1"), 110, True, "Divers"),
    ClientColumnDefinition("custom_2", "To define 2", ("define_2", "to_define_2", "custom_2"), 110, True, "Divers"),
    ClientColumnDefinition("custom_3", "To define 3", ("define_3", "to_define_3", "custom_3"), 110, True, "Divers"),
    ClientColumnDefinition("country_code", "Indicatif du pays", ("country_code", "phone_prefix"), 120, True, "Contact"),
    ClientColumnDefinition("mobile", "Portable", ("phone", "mobile", "portable", "customer_phone"), 130, True, "Contact"),
    ClientColumnDefinition("landline", "Fixe", ("tel", "telephone", "fixed_phone", "landline"), 120, True, "Contact"),
    ClientColumnDefinition("email", "Email", ("email", "mail", "customer_mail"), 210, True, "Contact"),
    ClientColumnDefinition("address", "Adresse", ("address", "adresse", "street", "remark"), 240, True, "Facturation"),
    ClientColumnDefinition("zip_code", "Code postal", ("zip", "zipcode", "postal_code", "codePostal"), 100, True, "Facturation"),
    ClientColumnDefinition("city", "Ville", ("city", "ville"), 130, True, "Facturation"),
    ClientColumnDefinition("country", "Pays", ("country", "pays", "country_name"), 90, True, "Facturation"),
    ClientColumnDefinition("remark", "Remarque", ("remark", "note", "comment"), 220, True, "Divers"),
    ClientColumnDefinition("business_number", "N° Commerce", ("business_number", "businessNumber", "commerce_number"), 140, True, "Facturation"),
    ClientColumnDefinition("company", "Société", ("company_name", "company", "societe", "shop_name", "name_company"), 180, True, "Identité"),
    ClientColumnDefinition("vat_number", "N° TVA", ("vat_num", "vat", "tva", "vat_number", "company_id"), 130, True, "Facturation"),
    ClientColumnDefinition("delivery_name", "Adresse de livraison: destinataire", ("delivery_name", "shipping_name", "receiver_name"), 190, True, "Livraison"),
    ClientColumnDefinition("delivery_phone", "Adresse de livraison: Téléphone", ("delivery_phone", "shipping_phone", "receiver_phone"), 150, True, "Livraison"),
    ClientColumnDefinition("delivery_address", "Adresse de livraison: adresse détaillée", ("delivery_address", "shipping_address", "receiver_address"), 260, True, "Livraison"),
    ClientColumnDefinition("delivery_zip", "Adresse de livraison: zip", ("delivery_zip", "shipping_zip", "receiver_zip"), 120, True, "Livraison"),
    ClientColumnDefinition("delivery_city", "Adresse de livraison: ville", ("delivery_city", "shipping_city", "receiver_city"), 160, True, "Livraison"),
    ClientColumnDefinition("delivery_country", "Adresse de livraison: pays", ("delivery_country", "shipping_country", "receiver_country"), 130, True, "Livraison"),
)

CLIENT_COLUMN_BY_KEY = {definition.key: definition for definition in CLIENT_COLUMN_DEFINITIONS}
DEFAULT_CLIENT_COLUMNS = tuple(definition.key for definition in CLIENT_COLUMN_DEFINITIONS if definition.default_visible)


def valid_client_column_keys(keys: list[str] | tuple[str, ...] | None) -> list[str]:
    if not keys:
        return list(DEFAULT_CLIENT_COLUMNS)
    valid = [key for key in keys if key in CLIENT_COLUMN_BY_KEY]
    return valid or list(DEFAULT_CLIENT_COLUMNS)


def microstore_client_field(client: PortalClient, key: str) -> str:
    direct = {
        "name": client.name,
        "company": client.company,
        "mobile": client.phone,
        "email": client.email,
        "address": client.address,
        "zip_code": client.zip_code,
        "city": client.city,
        "country": client.country,
        "vat_number": client.vat_number,
    }
    if key in direct and direct[key]:
        return str(direct[key]).strip()
    definition = CLIENT_COLUMN_BY_KEY.get(key)
    if not definition:
        return ""
    value = _field_from_raw(client.raw, definition.aliases)
    if value in (None, "") and key.startswith("delivery_"):
        value = _field_from_shipping(client.raw, key)
    if key == "discount" and value not in (None, ""):
        return _discount_label(value)
    if key == "created_at" and value not in (None, ""):
        return _date_label(value)
    return _stringify(value)


def microstore_client_search_text(client: PortalClient) -> str:
    values = [microstore_client_field(client, definition.key) for definition in CLIENT_COLUMN_DEFINITIONS]
    values.extend(_flatten_values(client.raw))
    return " ".join(value for value in values if value).lower()


def client_raw_field_examples(clients: list[PortalClient], max_examples: int = 1) -> list[tuple[str, str]]:
    examples: dict[str, list[str]] = {}
    for client in clients:
        for path, value in _iter_raw_fields(client.raw):
            text = _stringify(value)
            if not text:
                continue
            bucket = examples.setdefault(path, [])
            if len(bucket) < max_examples and text not in bucket:
                bucket.append(text[:120])
    return [(path, " | ".join(values)) for path, values in sorted(examples.items())]


def _field_from_raw(raw: Any, aliases: tuple[str, ...]) -> Any:
    if not isinstance(raw, dict):
        return ""
    for alias in aliases:
        value = _nested_get(raw, alias)
        if value not in (None, ""):
            return value
    for address in _address_entries(raw):
        for alias in aliases:
            value = _nested_get(address, alias)
            if value not in (None, ""):
                return value
    return ""


def _field_from_shipping(raw: dict[str, Any], key: str) -> Any:
    mappings = {
        "delivery_name": ("name",),
        "delivery_phone": ("phone", "tel", "mobile"),
        "delivery_address": ("address",),
        "delivery_zip": ("zip", "zipcode", "postal_code"),
        "delivery_city": ("city",),
        "delivery_country": ("country",),
    }
    aliases = mappings.get(key, ())
    for entry in _address_entries(raw):
        if str(entry.get("default", "")).lower() not in ("", "1", "true", "yes"):
            continue
        if key == "delivery_name":
            name = " ".join(part for part in (_stringify(entry.get("first_name")), _stringify(entry.get("last_name"))) if part)
            if name:
                return name
        for alias in aliases:
            value = _nested_get(entry, alias)
            if value not in (None, ""):
                return value
        if key == "delivery_address":
            address = " ".join(part for part in (_stringify(entry.get("address")), _stringify(entry.get("address2"))) if part)
            if address:
                return address
    return ""


def _address_entries(raw: dict[str, Any]) -> list[dict[str, Any]]:
    value = raw.get("address") if isinstance(raw, dict) else None
    if isinstance(value, dict):
        return [entry for entry in value.values() if isinstance(entry, dict)]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []


def _nested_get(raw: dict[str, Any], path: str) -> Any:
    current: Any = raw
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _discount_label(value: Any) -> str:
    text = _stringify(value)
    if text in {"1", "1.0", "1.00", "1.0000"}:
        return ""
    return text


def _date_label(value: Any) -> str:
    text = _stringify(value)
    if text.isdigit():
        timestamp = int(text)
        if timestamp > 0:
            return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d/%m/%Y")
    return text


def _stringify(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _flatten_values(raw: Any) -> list[str]:
    values: list[str] = []
    for _path, value in _iter_raw_fields(raw):
        text = _stringify(value)
        if text:
            values.append(text)
    return values


def _iter_raw_fields(raw: Any, prefix: str = ""):
    if isinstance(raw, dict):
        for key, value in raw.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_raw_fields(value, path)
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            path = f"{prefix}[{index}]"
            yield from _iter_raw_fields(value, path)
    else:
        yield prefix, raw
