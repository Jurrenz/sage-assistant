from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .models import Product
from .portal_orders import MicrostoreConnector, PortalApiError


class MicrostoreWriteError(RuntimeError):
    pass


@dataclass(frozen=True)
class MicrostoreProductPayload:
    action: str
    endpoint: str
    payload: dict[str, object]


class MicrostoreProductWriter:
    """Applies product changes through Microstore's confirmed form API."""

    def __init__(self, connector: MicrostoreConnector) -> None:
        self.connector = connector

    def build_payload(self, product: Product) -> MicrostoreProductPayload:
        microstore_id = _microstore_id(product)
        action = "create" if product.workflow_status == "to_create" or not microstore_id else "update"
        endpoint = "/goods/add" if action == "create" else "/goods/update"
        payload = _base_payload(product)
        if action == "update":
            payload["id"] = str(microstore_id)
            payload["del_id"] = []
            payload["sku"] = _update_skus(product)
        else:
            payload["sku"] = _create_skus(product)
        return MicrostoreProductPayload(action=action, endpoint=endpoint, payload=payload)

    def apply(self, product: Product) -> Product:
        payload = self.build_payload(product)
        try:
            if payload.action == "create":
                return self.connector.add_product(payload.payload)
            microstore_id = payload.payload.get("id")
            if not microstore_id:
                raise MicrostoreWriteError("Id Microstore absent pour la modification.")
            return self.connector.update_product(str(microstore_id), payload.payload)
        except PortalApiError as exc:
            raise MicrostoreWriteError(str(exc)) from exc

    def set_active(self, product: Product, active: bool) -> Product:
        microstore_id = _microstore_id(product)
        if not microstore_id:
            raise MicrostoreWriteError("Id Microstore absent: synchronise d'abord les produits Microstore.")
        try:
            return self.connector.set_product_active(str(microstore_id), active)
        except PortalApiError as exc:
            raise MicrostoreWriteError(str(exc)) from exc


def _base_payload(product: Product) -> dict[str, object]:
    price = _price_text(product.unit_price_ht)
    payload: dict[str, object] = {
        "item_ref": product.ref.strip().upper(),
        "goods_group_id": _goods_group_id(product),
        "cat_id": _category_id(product),
        "num_per_pack": str(product.package_size or 1),
        "name": product.name.strip(),
        "remark_material": product.composition.strip(),
        "remark_package": product.content_label.strip(),
        "weight": str(product.weight_grams or 0),
        "box1": 0,
        "box2": 0,
        "box3": 0,
        "new_order": 1,
    }
    if price:
        payload.update(
            {
                "price_1": price,
                "price_2": price,
                "price_3": price,
                "price_4": price,
            }
        )
    return payload


def _create_skus(product: Product) -> list[dict[str, object]]:
    price = _price_text(product.unit_price_ht) or "0.00"
    package_size = str(product.package_size or 1)
    return [
        {
            "color_id": _color_id(product),
            "size_id": "0",
            "color_alias": "",
            "num_per_pack": package_size,
            "num": "1",
            "price_1": price,
            "price_2": price,
            "price_3": price,
            "price_4": price,
            "price_5": "0.00",
            "price_6": "0.00",
            "price_7": "0.00",
            "price_8": "0.00",
            "sale_1": "1.0000",
            "sale_2": "1.0000",
            "sale_3": "1.0000",
            "sale_4": "1.0000",
            "price_in": "0.00",
            "order_by": 1,
        }
    ]


def _update_skus(product: Product) -> list[dict[str, object]]:
    raw_skus = _raw_skus(product)
    if not raw_skus:
        return _create_skus(product)
    skus: list[dict[str, object]] = []
    for index, sku in enumerate(raw_skus, start=1):
        skus.append(
            {
                "id": sku.get("id"),
                "color_id": sku.get("color_id") or _color_id(product),
                "size_id": sku.get("size_id") or "0",
                "color_alias": sku.get("color_alias") or "",
                "num_per_pack": str(product.package_size or sku.get("num_per_pack") or 1),
                "order_by": sku.get("order_by") or index,
            }
        )
    return skus


def _raw_skus(product: Product) -> list[dict[str, Any]]:
    sku = product.raw.get("sku") if isinstance(product.raw, dict) else None
    if isinstance(sku, list):
        return [item for item in sku if isinstance(item, dict)]
    if isinstance(sku, dict):
        return [item for item in sku.values() if isinstance(item, dict)]
    return []


def _microstore_id(product: Product) -> str:
    raw_id = product.raw.get("id") if isinstance(product.raw, dict) else None
    return str(raw_id or "").strip()


def _goods_group_id(product: Product) -> str:
    if isinstance(product.raw, dict):
        shops = product.raw.get("goods_shop_id")
        if isinstance(shops, list) and shops:
            return str(shops[0])
        raw_value = product.raw.get("goods_group_id")
        if raw_value:
            return str(raw_value)
    return "1"


def _category_id(product: Product) -> str:
    if isinstance(product.raw, dict):
        raw_value = product.raw.get("cat_id")
        if raw_value:
            return str(raw_value)
        for key in ("cate_info", "cat_info"):
            value = product.raw.get(key)
            if isinstance(value, dict) and value.get("id"):
                return str(value["id"])
    return "7"


def _color_id(product: Product) -> str:
    for sku in _raw_skus(product):
        if sku.get("color_id"):
            return str(sku["color_id"])
    return "9"


def _price_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"
