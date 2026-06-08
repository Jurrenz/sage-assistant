from __future__ import annotations

from dataclasses import dataclass

from .models import Product


class MicrostoreWriteNotEnabled(RuntimeError):
    pass


@dataclass(frozen=True)
class MicrostoreProductPayload:
    action: str
    endpoint: str
    payload: dict[str, object]


class MicrostoreProductWriter:
    """Builds Microstore write payloads without sending them yet."""

    def build_payload(self, product: Product) -> MicrostoreProductPayload:
        action = "create" if product.workflow_status == "to_create" else "update"
        endpoint = "/goods/add" if action == "create" else "/goods/update"
        payload: dict[str, object] = {
            "item_ref": product.ref,
            "name": product.name,
            "category": product.type_label,
            "unit_number": product.package_size,
            "price": str(product.unit_price_ht or ""),
            "package_content": product.content_label,
            "composition": product.composition,
            "color": product.color,
            "remark": product.remark,
        }
        if product.raw.get("id"):
            payload["id"] = product.raw["id"]
        return MicrostoreProductPayload(action=action, endpoint=endpoint, payload=payload)

    def apply(self, product: Product) -> MicrostoreProductPayload:
        payload = self.build_payload(product)
        raise MicrostoreWriteNotEnabled(
            "Écriture Microstore désactivée: payload goods/add/update à valider sur une référence test avant envoi réel."
        )
