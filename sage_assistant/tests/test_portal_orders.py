from __future__ import annotations

from decimal import Decimal

from app.portal_orders import (
    EFASHION_GRAPHQL_URL,
    MICROSTORE_API_BASE_URL,
    PFS_LOGIN_URL,
    EfashionConnector,
    MicrostoreConnector,
    PfsConnector,
    normalize_microstore_order_detail,
    normalize_microstore_order_summary,
    normalize_microstore_product,
    normalize_efashion_order_detail,
    normalize_efashion_order_summary,
    normalize_pfs_order_detail,
    normalize_pfs_order_summary,
)
from app.db import Database
from app.models import Product, SageMapping
from app.resolver import Resolver


def test_normalize_efashion_order_summary_and_detail():
    summary = normalize_efashion_order_summary(
        {
            "id_commande": "10599598",
            "id_commande_name": "F0917C488978A85509V465",
            "montantTotal": 78,
            "acheteur": {"nomSociete": "SARL SATELLITE "},
            "dateCommande": "2026-06-05T16:48:37.000Z",
            "commandeStatut": {"statut_fr": "Commande expédiée"},
        }
    )

    assert summary.source == "eFashion"
    assert summary.order_id == "10599598"
    assert summary.order_number == "F0917C488978A85509V465"
    assert summary.customer == "SARL SATELLITE"
    assert summary.created_at == "2026-06-05T16:48:37Z"
    assert summary.total_amount == Decimal("78")
    assert summary.status == "Commande expédiée"

    order = normalize_efashion_order_detail(
        {
            "data": {
                "commandeById": {
                    "id_commande": "10599598",
                    "id_commande_name": "F0917C488978A85509V465",
                    "montantTotal": 78,
                    "acheteur": {"nomSociete": "SARL SATELLITE "},
                    "dateCommande": "2026-06-05T16:48:37.000Z",
                    "lignes": [
                        {
                            "reference": "FL633-5",
                            "quantite_pack": 1,
                            "quantite_total": 12,
                            "prix": 6.5,
                            "prixLigne": 78,
                            "couleur_FR": "Couleurs mélangées",
                            "categorie": "Robes courtes",
                        }
                    ],
                }
            }
        }
    )

    assert order.source == "eFashion"
    assert order.order_id == "10599598"
    assert len(order.lines) == 1
    line = order.lines[0]
    assert line.ref == "FL633-5"
    assert line.category == "Robes courtes"
    assert line.package_count == 1
    assert line.package_size == 12
    assert line.quantity_pieces == 12
    assert line.unit_price_ht == Decimal("6.5")
    assert line.to_order_row().ref == "FL633-5"


def test_normalize_pfs_order_summary_and_detail():
    summary = normalize_pfs_order_summary(
        {
            "id": "ord_a1535bcd08c2c400ba3a8a9063d6",
            "order_no": "PO#41995819",
            "creation_date": "2026-06-05 10:36:08",
            "customer": "LISEN.DK A/S",
            "validated_vat": 900,
            "status": "SENT",
        }
    )

    assert summary.source == "PFS"
    assert summary.order_id == "ord_a1535bcd08c2c400ba3a8a9063d6"
    assert summary.order_number == "PO#41995819"
    assert summary.customer == "LISEN.DK A/S"
    assert summary.created_at == "2026-06-05T10:36:08"
    assert summary.total_amount == Decimal("900")
    assert summary.status == "SENT"

    order = normalize_pfs_order_detail(
        {
            "success": True,
            "data": {
                "id": "ord_a1535bcd08c2c400ba3a8a9063d6",
                "order_no": "PO#41995819",
                "created_at": "2026-06-05T08:36:08Z",
                "status": "SENT",
                "customer": {"shop": "LISEN.DK A/S"},
                "validated_vat": 900,
                "items_by_brand": [
                    {
                        "name": "S.Z FASHION",
                        "products": [
                            {
                                "reference": "FL730-1",
                                "category": {"labels": {"fr": "Robes"}},
                                "total_ordered_qty": 10,
                                "total_validated_qty": 10,
                                "total_validated_price": 900,
                                "validated": {"pieces": 120, "packs": 10},
                                "items": [
                                    {
                                        "name": "Robe longue à motifs",
                                        "pieces": 12,
                                        "qty_ordered": 10,
                                        "qty_validated": 10,
                                        "price_sale": {"unit": {"value": 7.5, "currency": "EUR"}},
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        }
    )

    assert order.source == "PFS"
    assert order.order_number == "PO#41995819"
    assert len(order.lines) == 1
    line = order.lines[0]
    assert line.ref == "FL730-1"
    assert line.category == "Robes"
    assert line.description == "Robe longue à motifs"
    assert line.package_count == 10
    assert line.package_size == 12
    assert line.quantity_pieces == 120
    assert line.unit_price_ht == Decimal("7.5")
    assert line.to_order_row().package_count == 10


def test_normalize_microstore_product_summary_and_detail_1001627():
    product = normalize_microstore_product(
        {
            "item_ref": "CM55-9",
            "name": "",
            "unit_number": "12",
            "cate_info": {"name": "CHEMISES / TUNIQUES"},
            "price_range": {"range": ["6.50"]},
        }
    )
    assert product is not None
    assert product.ref == "CM55-9"
    assert product.type_label == "CHEMISES / TUNIQUES"
    assert product.package_size == 12
    assert product.unit_price_ht == Decimal("6.50")

    summary = normalize_microstore_order_summary(
        {
            "id": "1001627",
            "number": "1001627",
            "doc_sn": "1001627",
            "customer_name": "ROMIE",
            "ctime": "1780042663",
            "order_pack_num": "8",
            "goods_quantity_x1": "96",
            "calc_price": "492.00",
        }
    )
    assert summary.source == "Microstore"
    assert summary.order_id == "1001627"
    assert summary.order_number == "1001627"
    assert summary.customer == "ROMIE"
    assert summary.created_at == "2026-05-29T08:17:43Z"
    assert summary.total_amount == Decimal("492.00")

    order = normalize_microstore_order_detail(
        {
            "err": 0,
            "data": {
                "doc_info": {
                    "id": "1001627",
                    "number": "1001627",
                    "doc_sn": "1001627",
                    "client_info": {
                        "company_name": "ROMIE",
                        "email": "cate.fesquet@gmail.com",
                        "phone": "650616033",
                    },
                    "ctime": "1780042663",
                    "total_price": "492.00",
                    "goods_info": [
                        {
                            "item_ref": "CM55-9",
                            "quantity": "2",
                            "quantity_pack": "2",
                            "unit_number": "12",
                            "price": "6.50",
                            "sale_sub_price": "156.00000",
                        },
                        {
                            "item_ref": "FL96-9",
                            "quantity": "1",
                            "quantity_pack": "1",
                            "unit_number": "12",
                            "price": "4.00",
                            "sale_price": "5.00",
                            "sale_sub_price": "48.00000",
                        },
                    ],
                }
            },
        }
    )
    assert order.source == "Microstore"
    assert order.customer == "ROMIE"
    assert order.total_amount == Decimal("492.00")
    assert len(order.lines) == 2
    assert order.lines[0].ref == "CM55-9"
    assert order.lines[0].package_count == 2
    assert order.lines[0].package_size == 12
    assert order.lines[0].quantity_pieces == 24
    assert order.lines[0].unit_price_ht == Decimal("6.50")
    assert order.lines[1].ref == "FL96-9"
    assert order.lines[1].quantity_pieces == 12
    assert order.lines[1].unit_price_ht == Decimal("4.00")


def test_normalize_microstore_disabled_product_remains_resolvable():
    product = normalize_microstore_product(
        {
            "item_ref": "JY96",
            "name": "",
            "unit_number": "12",
            "disable": "1780912911",
            "cate_info": {"name": "ROBES LONGUES"},
            "price_range": {"range": ["7.50"]},
            "brand": "SZ",
            "season": "ETE",
            "origin_country": "China",
            "utime": "1780913000",
        }
    )

    assert product is not None
    assert product.ref == "JY96"
    assert product.type_label == "ROBES LONGUES"
    assert product.package_size == 12
    assert product.unit_price_ht == Decimal("7.50")
    assert product.active is True
    assert product.microstore_status == "disabled"
    assert product.brand == "SZ"
    assert product.season == "ETE"
    assert product.origin_country == "China"
    assert product.last_microstore_modified_at == "2026-06-08T10:03:20Z"


class FakeHttp:
    def __init__(self) -> None:
        self.calls = []
        self.headers = {}

    def set_bearer_token(self, token):
        self.headers["Authorization"] = f"Bearer {token}"

    def set_header(self, name, value):
        self.headers[name] = value

    def post_json(self, url, payload):
        self.calls.append(("POST", url, payload))
        if url == PFS_LOGIN_URL:
            return {
                "access_token": "fake-token",
                "expires_at": "2026-06-06T10:00:00Z",
                "user": {"name": "Utilisateur PFS", "email": "user@example.com"},
            }
        if url == EFASHION_GRAPHQL_URL and "mutation Login" in payload["query"]:
            return {"data": {"login": {"user": {"email": "user@example.com", "nomBoutique": "Boutique test"}}}}
        if "commandes" in payload["query"]:
            return {"data": {"commandes": {"data": [{"id_commande": "1", "id_commande_name": "EF1"}]}}}
        return {"data": {"commandeById": {"id_commande": "1", "id_commande_name": "EF1", "lignes": []}}}

    def post_form(self, url, payload, params=None):
        self.calls.append(("POST_FORM", url, {"payload": payload, "params": params}))
        if url.endswith("/goods/get"):
            return {
                "err": 0,
                "info": {
                    "id": str(payload["id"]),
                    "item_ref": "CM55-9",
                    "name": "Chemise test",
                    "num_per_pack": "12",
                    "cat_info": {"id": "1", "name": "CHEMISES / TUNIQUES"},
                    "price_1": "6.50",
                    "disable": "0",
                    "sku": [{"id": "sku-1", "color_id": "9", "size_id": "0", "num_per_pack": "12"}],
                },
                "msg": "Succès",
            }
        if url.endswith("/goods/add"):
            return {"err": 0, "id": "new-id", "msg": "Succès"}
        if url.endswith("/goods/update") or url.endswith("/goods/batch_set_attribute"):
            return {"err": 0, "msg": "Succès"}
        return {"err": 9999, "msg": "unexpected"}

    def get_json(self, url, params=None):
        self.calls.append(("GET", url, params))
        if url.endswith("/goods/get_by_order"):
            return {
                "err": 0,
                "is_last": 1,
                "list": [
                    {
                        "item_ref": "CM55-9",
                        "unit_number": "12",
                        "cate_info": {"name": "CHEMISES / TUNIQUES"},
                        "price_range": {"range": ["6.50"]},
                    }
                ],
            }
        if url.endswith("/order/new_view_all"):
            return {
                "err": 0,
                "is_last": 1,
                "list": [{"id": "1001627", "doc_sn": "1001627", "customer_name": "ROMIE", "calc_price": "492.00"}],
            }
        if "/pluginsWeb/orderInfo/" in url:
            return {
                "err": 0,
                "data": {
                    "doc_info": {
                        "id": "1001627",
                        "doc_sn": "1001627",
                        "client_info": {"company_name": "ROMIE"},
                        "goods_info": [{"item_ref": "CM55-9", "quantity_pack": "2", "unit_number": "12", "price": "6.50"}],
                    }
                },
            }
        if url.endswith("/orders/listOrders"):
            return {"data": [{"id": "ord_1", "order_no": "PO#1"}]}
        return {"success": True, "data": {"id": "ord_1", "order_no": "PO#1", "items_by_brand": []}}


def test_connectors_call_expected_endpoints():
    fake = FakeHttp()

    microstore = MicrostoreConnector("micro-token", fake)
    assert microstore.list_products()[0].ref == "CM55-9"
    assert microstore.list_orders()[0].order_number == "1001627"
    assert microstore.get_order("1001627").lines[0].quantity_pieces == 24
    assert microstore.get_product("123").ref == "CM55-9"
    assert microstore.add_product({"item_ref": "CM55-9"}).ref == "CM55-9"
    assert microstore.update_product("123", {"id": "123", "item_ref": "CM55-9"}).ref == "CM55-9"
    assert microstore.set_product_active("123", False).ref == "CM55-9"

    efashion = EfashionConnector(fake)
    assert efashion.list_orders()[0].order_number == "EF1"
    assert efashion.get_order("1").order_number == "EF1"

    pfs = PfsConnector(fake)
    assert pfs.list_orders(start_date="2026-06-01", end_date="2026-06-05")[0].order_number == "PO#1"
    assert pfs.get_order("ord_1").order_number == "PO#1"

    methods_urls = [(method, url) for method, url, _payload in fake.calls]
    assert ("GET", f"{MICROSTORE_API_BASE_URL}/goods/get_by_order") in methods_urls
    assert ("GET", f"{MICROSTORE_API_BASE_URL}/order/new_view_all") in methods_urls
    assert ("GET", f"{MICROSTORE_API_BASE_URL}/pluginsWeb/orderInfo/1001627") in methods_urls
    assert ("POST_FORM", f"{MICROSTORE_API_BASE_URL}/goods/get") in methods_urls
    assert ("POST_FORM", f"{MICROSTORE_API_BASE_URL}/goods/add") in methods_urls
    assert ("POST_FORM", f"{MICROSTORE_API_BASE_URL}/goods/update") in methods_urls
    assert ("POST_FORM", f"{MICROSTORE_API_BASE_URL}/goods/batch_set_attribute") in methods_urls
    assert ("POST", "https://wapi.efashion-paris.com/graphql") in methods_urls
    assert ("GET", "https://wholesaler-api.parisfashionshops.com/api/v1/orders/listOrders") in methods_urls
    assert ("GET", "https://wholesaler-api.parisfashionshops.com/api/v1/orders/ord_1") in methods_urls


def test_microstore_connector_fetches_active_and_disabled_products():
    fake = FakeHttp()

    MicrostoreConnector("micro-token", fake).list_products()

    product_calls = [payload for method, url, payload in fake.calls if method == "GET" and url.endswith("/goods/get_by_order")]
    statuses = [payload["goods_status"] for payload in product_calls]

    assert any("disable=0" in status for status in statuses)
    assert any("disable=1" in status for status in statuses)


def test_pfs_unvalidated_order_uses_ordered_quantity():
    order = normalize_pfs_order_detail(
        {
            "success": True,
            "data": {
                "id": "ord_2",
                "order_no": "PO#2",
                "customer": {"shop": "Client test"},
                "items_by_brand": [
                    {
                        "products": [
                            {
                                "reference": "JY96",
                                "category": {"labels": {"fr": "Robes"}},
                                "total_ordered_qty": 1,
                                "total_validated_qty": None,
                                "validated": {"pieces": None, "packs": None},
                                "items": [
                                    {
                                        "name": "Robe test",
                                        "pieces": 12,
                                        "qty_ordered": 1,
                                        "qty_validated": None,
                                        "price_sale": {"unit": {"value": 7.5}},
                                    }
                                ],
                            }
                        ]
                    }
                ],
            },
        }
    )

    assert order.lines[0].package_count == 1
    assert order.lines[0].package_size == 12
    assert order.lines[0].quantity_pieces == 12
    assert order.lines[0].unit_price_ht == Decimal("7.5")


def test_pfs_zero_validated_pieces_falls_back_to_ordered_quantity():
    order = normalize_pfs_order_detail(
        {
            "success": True,
            "data": {
                "id": "ord_3",
                "order_no": "PO#3",
                "items_by_brand": [
                    {
                        "products": [
                            {
                                "reference": "FL329-2",
                                "category": {"labels": {"fr": "Robes"}},
                                "total_ordered_qty": 1,
                                "total_validated_qty": 0,
                                "validated": {"pieces": 0, "packs": 0},
                                "items": [{"name": "Robe", "pieces": 12, "qty_ordered": 1, "price_sale": {"unit": {"value": 4}}}],
                            }
                        ]
                    }
                ],
            },
        }
    )

    assert order.lines[0].package_count == 1
    assert order.lines[0].quantity_pieces == 12


def test_portal_api_logins_prepare_sessions_without_storing_passwords():
    fake = FakeHttp()

    efashion_session = EfashionConnector(fake).login("user@example.com", "secret")
    pfs_session = PfsConnector(fake).login("user@example.com", "secret")

    assert efashion_session.source == "eFashion"
    assert efashion_session.user_label == "Boutique test"
    assert pfs_session.source == "PFS"
    assert pfs_session.user_label == "Utilisateur PFS"
    assert fake.headers["Authorization"] == "Bearer fake-token"
    assert "access_token" not in pfs_session.raw
    assert "secret" not in str(efashion_session.raw)
    assert "secret" not in str(pfs_session.raw)


def test_portal_order_rows_resolve_to_invoice_lines(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(
        [
            Product(
                id=None,
                ref="FL730-1",
                type_label="ROBES LONGUES",
                name="",
                unit_price_ht=Decimal("7.5"),
                package_size=12,
            )
        ]
    )
    db.upsert_mapping(SageMapping("ROBES LONGUES", "RO", "ROBE / TUNIC"))
    portal_order = normalize_pfs_order_detail(
        {
            "success": True,
            "data": {
                "id": "ord_1",
                "order_no": "PO#1",
                "customer": {"shop": "Client test"},
                "items_by_brand": [
                    {
                        "products": [
                            {
                                "reference": "FL730-1",
                                "total_validated_qty": 10,
                                "validated": {"pieces": 120, "packs": 10},
                                "items": [
                                    {
                                        "name": "Robe longue à motifs",
                                        "pieces": 12,
                                        "price_sale": {"unit": {"value": 7.5}},
                                    }
                                ],
                            }
                        ]
                    }
                ],
            },
        }
    )

    lines = Resolver(db).lines_from_order_rows(portal_order.to_order_rows(), source=portal_order.source)

    assert len(lines) == 1
    assert lines[0].source == "PFS"
    assert lines[0].sage_code == "RO"
    assert lines[0].quantity_pieces == 120
    assert lines[0].package_count == 10
    assert lines[0].unit_price_ht == Decimal("7.5")
    assert lines[0].validation_status == "ok"
    db.close()


def test_portal_lines_can_resolve_without_local_product_when_category_is_mapped(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_mapping(SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"))

    pfs_order = normalize_pfs_order_detail(
        {
            "success": True,
            "data": {
                "id": "ord_1",
                "order_no": "PO#1",
                "customer": {"shop": "Client test"},
                "items_by_brand": [
                    {
                        "products": [
                            {
                                "reference": "FL730-1",
                                "category": {"labels": {"fr": "Robes"}},
                                "total_validated_qty": 10,
                                "validated": {"pieces": 120, "packs": 10},
                                "items": [{"name": "Robe test", "pieces": 12, "price_sale": {"unit": {"value": 7.5}}}],
                            }
                        ]
                    }
                ],
            },
        }
    )
    efashion_order = normalize_efashion_order_detail(
        {
            "id_commande": "1",
            "id_commande_name": "EF1",
            "lignes": [
                {
                    "reference": "FL633-5",
                    "quantite_pack": 1,
                    "quantite_total": 12,
                    "prix": 6.5,
                    "categorie": "Robes courtes",
                }
            ],
        }
    )

    pfs_lines = Resolver(db).lines_from_portal_lines(pfs_order.lines, source=pfs_order.source)
    efashion_lines = Resolver(db).lines_from_portal_lines(efashion_order.lines, source=efashion_order.source)

    assert pfs_lines[0].validation_status == "ok"
    assert pfs_lines[0].product_id == 0
    assert pfs_lines[0].sage_code == "RO"
    assert pfs_lines[0].description == "ROBE / TUNIC FL730-1"
    assert efashion_lines[0].validation_status == "ok"
    assert efashion_lines[0].product_id == 0
    assert efashion_lines[0].sage_code == "RO"
    assert efashion_lines[0].description == "ROBE / TUNIC FL633-5"
    db.close()


def test_portal_line_with_unknown_category_is_not_marked_as_unresolved_reference(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    line = Resolver(db).line_from_portal_line(
        normalize_pfs_order_detail(
            {
                "success": True,
                "data": {
                    "id": "ord_1",
                    "order_no": "PO#1",
                    "items_by_brand": [
                        {
                            "products": [
                                {
                                    "reference": "EXT123",
                                    "category": {"labels": {"fr": "Categorie inconnue"}},
                                    "total_validated_qty": 1,
                                    "validated": {"pieces": 12, "packs": 1},
                                    "items": [{"name": "Produit externe", "pieces": 12, "price_sale": {"unit": {"value": 5}}}],
                                }
                            ]
                        }
                    ],
                },
            }
        ).lines[0],
        source="PFS",
    )

    assert "reference non resolue" not in line.validation_message
    assert "description absente" not in line.validation_message
    assert "code Sage absent" in line.validation_message
    db.close()
