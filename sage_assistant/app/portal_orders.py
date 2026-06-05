from __future__ import annotations

import json
from http.cookiejar import CookieJar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib import request

from .excel_import import OrderRow, normalize_ref
from .models import Product


EFASHION_GRAPHQL_URL = "https://wapi.efashion-paris.com/graphql"
MICROSTORE_API_BASE_URL = "https://api2.dokkr.net/index.php"
PFS_API_BASE_URL = "https://wholesaler-api.parisfashionshops.com/api/v1"
PFS_LOGIN_URL = "https://client.parisfashionshops.com/api/v1/oauth/login?lang=fr"
DEFAULT_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


@dataclass(frozen=True)
class PortalOrderLine:
    ref: str
    category: str = ""
    description: str = ""
    package_count: int = 0
    package_size: int | None = None
    quantity_pieces: int | None = None
    unit_price_ht: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_order_row(self) -> OrderRow:
        return OrderRow(
            ref=self.ref,
            package_count=self.package_count,
            package_size=self.package_size,
            quantity_pieces=self.quantity_pieces,
            unit_price_ht=self.unit_price_ht,
        )


@dataclass(frozen=True)
class PortalOrderSummary:
    source: str
    order_id: str
    order_number: str
    customer: str = ""
    created_at: str = ""
    status: str = ""
    total_amount: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PortalOrder:
    source: str
    order_id: str
    order_number: str
    customer: str = ""
    created_at: str = ""
    status: str = ""
    total_amount: Decimal | None = None
    lines: list[PortalOrderLine] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_order_rows(self) -> list[OrderRow]:
        return [line.to_order_row() for line in self.lines]


class PortalApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortalSession:
    source: str
    user_label: str = ""
    expires_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value).replace(",", ".")))
    except (InvalidOperation, ValueError):
        return None


def _label(labels: dict[str, Any] | None, language: str = "fr") -> str:
    if not labels:
        return ""
    return str(labels.get(language) or labels.get("en") or next(iter(labels.values()), "") or "").strip()


def _iso_date(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    parse_text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(parse_text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return text
    if parsed.tzinfo is None:
        return parsed.replace(microsecond=0).isoformat()
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_efashion_order_summary(raw: dict[str, Any]) -> PortalOrderSummary:
    status = raw.get("commandeStatut") or {}
    buyer = raw.get("acheteur") or {}
    return PortalOrderSummary(
        source="eFashion",
        order_id=str(raw.get("id_commande") or ""),
        order_number=str(raw.get("id_commande_name") or raw.get("id_commande") or ""),
        customer=str(buyer.get("nomSociete") or "").strip(),
        created_at=_iso_date(raw.get("dateCommande") or raw.get("dateCreation")),
        status=str(status.get("statut_fr") or raw.get("statut") or ""),
        total_amount=_decimal(raw.get("montantApresRemise") or raw.get("montantTotal") or raw.get("montantCA")),
        raw=raw,
    )


def normalize_efashion_order_detail(raw: dict[str, Any]) -> PortalOrder:
    detail = raw.get("data", {}).get("commandeById") if "data" in raw else raw
    if not isinstance(detail, dict):
        raise ValueError("Detail eFashion invalide.")
    summary = normalize_efashion_order_summary(detail)
    lines: list[PortalOrderLine] = []
    for item in detail.get("lignes") or []:
        ref = normalize_ref(item.get("reference") or item.get("reference_base"))
        if not ref:
            continue
        package_count = _int(item.get("quantite_pack")) or 0
        quantity_pieces = _int(item.get("quantite_total"))
        package_size = (quantity_pieces // package_count) if package_count and quantity_pieces else None
        unit_price = _decimal(item.get("prixReduit")) or _decimal(item.get("prix"))
        lines.append(
            PortalOrderLine(
                ref=ref,
                category=str(item.get("categorie") or "").strip(),
                description=" ".join(part for part in (ref, str(item.get("couleur_FR") or "").strip()) if part),
                package_count=package_count,
                package_size=package_size,
                quantity_pieces=quantity_pieces,
                unit_price_ht=unit_price,
                raw=item,
            )
        )
    return PortalOrder(
        source=summary.source,
        order_id=summary.order_id,
        order_number=summary.order_number,
        customer=summary.customer,
        created_at=summary.created_at,
        status=summary.status,
        total_amount=summary.total_amount,
        lines=lines,
        raw=detail,
    )


def normalize_pfs_order_summary(raw: dict[str, Any]) -> PortalOrderSummary:
    return PortalOrderSummary(
        source="PFS",
        order_id=str(raw.get("id") or ""),
        order_number=str(raw.get("order_no") or raw.get("id") or ""),
        customer=str(raw.get("customer") or "").strip(),
        created_at=_iso_date(raw.get("creation_date") or raw.get("created_at")),
        status=str(raw.get("status") or ""),
        total_amount=_decimal(raw.get("validated_vat") or raw.get("order_vat")),
        raw=raw,
    )


def normalize_pfs_order_detail(raw: dict[str, Any]) -> PortalOrder:
    detail = raw.get("data") if raw.get("success") and isinstance(raw.get("data"), dict) else raw
    if not isinstance(detail, dict):
        raise ValueError("Detail PFS invalide.")
    customer = detail.get("customer") or {}
    lines: list[PortalOrderLine] = []
    for brand in detail.get("items_by_brand") or []:
        for product in brand.get("products") or []:
            ref = normalize_ref(product.get("reference"))
            if not ref:
                continue
            package_count = _int(product.get("total_validated_qty")) or _int(product.get("total_ordered_qty")) or 0
            validated = product.get("validated") or {}
            quantity_pieces = _int(validated.get("pieces"))
            if quantity_pieces is None:
                quantity_pieces = _int(product.get("total_validated_qty"))
            first_item = (product.get("items") or [{}])[0]
            package_size = _int(first_item.get("pieces"))
            if package_size is None and package_count and quantity_pieces:
                package_size = quantity_pieces // package_count
            unit_price = _decimal((first_item.get("price_sale") or {}).get("unit", {}).get("value"))
            if unit_price is None and quantity_pieces:
                total = _decimal(product.get("total_validated_price") or product.get("total_ordered_price"))
                unit_price = total / Decimal(quantity_pieces) if total is not None else None
            lines.append(
                PortalOrderLine(
                    ref=ref,
                    category=_label((product.get("category") or {}).get("labels")),
                    description=str(first_item.get("name") or ref).strip(),
                    package_count=package_count,
                    package_size=package_size,
                    quantity_pieces=quantity_pieces,
                    unit_price_ht=unit_price,
                    raw=product,
                )
            )
    return PortalOrder(
        source="PFS",
        order_id=str(detail.get("id") or ""),
        order_number=str(detail.get("order_no") or detail.get("id") or ""),
        customer=str(customer.get("shop") or customer.get("name") or "").strip(),
        created_at=_iso_date(detail.get("created_at")),
        status=str(detail.get("status") or ""),
        total_amount=_decimal(detail.get("validated_vat") or detail.get("order_vat")),
        lines=lines,
        raw=detail,
    )


def _microstore_unix_to_iso(value: Any) -> str:
    seconds = _int(value)
    if seconds is None:
        return _iso_date(value)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_microstore_product(raw: dict[str, Any]) -> Product | None:
    ref = normalize_ref(raw.get("item_ref"))
    if not ref:
        return None
    category = raw.get("cate_info") or {}
    price_range = raw.get("price_range") or {}
    prices = price_range.get("range") if isinstance(price_range, dict) else []
    unit_price = _decimal(prices[0]) if prices else None
    if unit_price is None:
        sku = raw.get("sku") or {}
        if isinstance(sku, dict):
            for item in sku.values():
                if isinstance(item, dict):
                    unit_price = _decimal(item.get("price_1"))
                    if unit_price is not None:
                        break
    return Product(
        id=None,
        ref=ref,
        type_label=str(category.get("name") or "").strip(),
        name=str(raw.get("name") or "").strip(),
        unit_price_ht=unit_price,
        package_size=_int(raw.get("unit_number")),
    )


def normalize_microstore_order_summary(raw: dict[str, Any]) -> PortalOrderSummary:
    return PortalOrderSummary(
        source="Microstore",
        order_id=str(raw.get("id") or raw.get("number") or ""),
        order_number=str(raw.get("doc_sn") or raw.get("number") or raw.get("id") or ""),
        customer=str(raw.get("customer_name") or "").strip(),
        created_at=_microstore_unix_to_iso(raw.get("ctime")),
        status=str(raw.get("show") or raw.get("goods_status") or raw.get("shipping_status") or ""),
        total_amount=_decimal(raw.get("calc_price") or raw.get("total_price")),
        raw=raw,
    )


def normalize_microstore_order_detail(raw: dict[str, Any]) -> PortalOrder:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    doc = data.get("doc_info") if isinstance(data, dict) else None
    if not isinstance(doc, dict):
        raise ValueError("Detail Microstore invalide.")
    client = doc.get("client_info") or {}
    lines: list[PortalOrderLine] = []
    for item in doc.get("goods_info") or []:
        ref = normalize_ref(item.get("item_ref"))
        if not ref:
            continue
        package_count = _int(item.get("quantity_pack") or item.get("quantity")) or 0
        package_size = _int(item.get("unit_number") or item.get("old_unit_number"))
        quantity_pieces = _int(item.get("quantity_one"))
        if not quantity_pieces and package_count and package_size:
            quantity_pieces = package_count * package_size
        unit_price = _decimal(item.get("price"))
        lines.append(
            PortalOrderLine(
                ref=ref,
                category="",
                description=ref,
                package_count=package_count,
                package_size=package_size,
                quantity_pieces=quantity_pieces,
                unit_price_ht=unit_price,
                raw=item,
            )
        )
    return PortalOrder(
        source="Microstore",
        order_id=str(doc.get("id") or ""),
        order_number=str(doc.get("doc_sn") or doc.get("number") or doc.get("id") or ""),
        customer=str(client.get("company_name") or client.get("first_name") or "").strip(),
        created_at=_microstore_unix_to_iso(doc.get("ctime")),
        status=str(doc.get("show") or doc.get("goods_status") or doc.get("shipping_status") or ""),
        total_amount=_decimal(doc.get("total_price") or doc.get("goods_total_price")),
        lines=lines,
        raw=doc,
    )


class JsonHttpClient:
    def __init__(self, headers: dict[str, str] | None = None, timeout: int = 30, cookie_jar: CookieJar | None = None) -> None:
        self.headers = {**DEFAULT_BROWSER_HEADERS, **(headers or {})}
        self.timeout = timeout
        self.cookie_jar = cookie_jar or CookieJar()
        self.opener = request.build_opener(request.HTTPCookieProcessor(self.cookie_jar))

    def set_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    def set_bearer_token(self, token: str) -> None:
        self.set_header("Authorization", f"Bearer {token}")

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params:
            from urllib.parse import urlencode

            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode({key: value for key, value in params.items() if value is not None})}"
        return self._request_json("GET", url)

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", url, payload)

    def _request_json(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", **self.headers}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise PortalApiError(f"Erreur API {method} {url}: {exc}") from exc


class EfashionConnector:
    def __init__(self, http: JsonHttpClient | None = None) -> None:
        self.http = http or JsonHttpClient()
        self.session: PortalSession | None = None

    def login(self, email: str, password: str, remember_me: bool = True) -> PortalSession:
        body = self.http.post_json(
            EFASHION_GRAPHQL_URL,
            {
                "query": EFASHION_LOGIN_QUERY,
                "variables": {"email": email, "password": password, "rememberMe": remember_me},
            },
        )
        user = body.get("data", {}).get("login", {}).get("user")
        if not isinstance(user, dict):
            raise PortalApiError("Connexion eFashion echouee.")
        self.session = PortalSession(
            source="eFashion",
            user_label=str(user.get("nomBoutique") or user.get("email") or email),
            raw=user,
        )
        return self.session

    def list_orders(self, page: int = 1, limit: int = 25, filters: dict[str, Any] | None = None) -> list[PortalOrderSummary]:
        body = self.http.post_json(
            EFASHION_GRAPHQL_URL,
            {
                "query": EFASHION_LIST_ORDERS_QUERY,
                "variables": {"page": page, "limit": limit, "filters": filters},
            },
        )
        orders = body.get("data", {}).get("commandes", {}).get("data") or []
        return [normalize_efashion_order_summary(order) for order in orders]

    def get_order(self, order_id: str | int) -> PortalOrder:
        body = self.http.post_json(
            EFASHION_GRAPHQL_URL,
            {"query": EFASHION_ORDER_DETAIL_QUERY, "variables": {"id": int(order_id)}},
        )
        return normalize_efashion_order_detail(body)


class MicrostoreConnector:
    def __init__(
        self,
        token: str = "",
        http: JsonHttpClient | None = None,
        api_base_url: str = MICROSTORE_API_BASE_URL,
    ) -> None:
        self.token = token.strip()
        self.http = http or JsonHttpClient()
        self.api_base_url = api_base_url.rstrip("/")

    def set_token(self, token: str) -> None:
        self.token = token.strip()

    def _params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise PortalApiError("Token Microstore absent.")
        return {"key": self.token, "pid": 5, "lang": "fr", **(params or {})}

    def list_products(self, page_size: int = 200) -> list[Product]:
        products: list[Product] = []
        page = 1
        while True:
            body = self.http.get_json(
                f"{self.api_base_url}/goods/get_by_order",
                self._params(
                    {
                        "bi_key": "productList-instock",
                        "days": -1,
                        "show_sku": 1,
                        "order": "utime",
                        "isasc": 0,
                        "page": page,
                        "page_num": page_size,
                        "goods_status": json.dumps({"options": ["disable=0"], "allSelected": 0}, separators=(",", ":")),
                        "dimension": json.dumps({"options": ["goods"], "allSelected": 0}, separators=(",", ":")),
                    }
                ),
            )
            if int(body.get("err") or 0) != 0:
                raise PortalApiError(str(body.get("msg") or body.get("debug_msg") or "Erreur produits Microstore"))
            for raw in body.get("list") or []:
                product = normalize_microstore_product(raw)
                if product:
                    products.append(product)
            if int(body.get("is_last") or 0) == 1 or not body.get("list"):
                break
            page += 1
        return products

    def list_orders(self, days: int = 45, page_size: int = 50) -> list[PortalOrderSummary]:
        end = datetime.now().date()
        start = end - timedelta(days=max(days, 1))
        summaries: list[PortalOrderSummary] = []
        page = 1
        while True:
            body = self.http.get_json(
                f"{self.api_base_url}/order/new_view_all",
                self._params(
                    {
                        "bi_key": "documentList",
                        "page": page,
                        "page_num": page_size,
                        "type": "custom",
                        "sday": start.isoformat(),
                        "eday": end.isoformat(),
                        "order_type": json.dumps({"options": ["sale_order"], "allSelected": 0}, separators=(",", ":")),
                    }
                ),
            )
            if int(body.get("err") or 0) != 0:
                raise PortalApiError(str(body.get("msg") or body.get("debug_msg") or "Erreur commandes Microstore"))
            summaries.extend(normalize_microstore_order_summary(order) for order in body.get("list") or [])
            if int(body.get("is_last") or 0) == 1 or not body.get("list"):
                break
            page += 1
        return summaries

    def get_order(self, order_id: str | int) -> PortalOrder:
        body = self.http.get_json(
            f"{self.api_base_url}/pluginsWeb/orderInfo/{order_id}",
            self._params({"data_type": "json"}),
        )
        if int(body.get("err") or 0) != 0:
            raise PortalApiError(str(body.get("msg") or body.get("debug_msg") or "Erreur detail Microstore"))
        return normalize_microstore_order_detail(body)


class PfsConnector:
    def __init__(self, http: JsonHttpClient | None = None, api_base_url: str = PFS_API_BASE_URL) -> None:
        self.http = http or JsonHttpClient()
        self.api_base_url = api_base_url.rstrip("/")
        self.session: PortalSession | None = None

    def login(self, email: str, password: str) -> PortalSession:
        self.http.set_header("Referer", "https://parisfashionshops.com/")
        body = self.http.post_json(PFS_LOGIN_URL, {"email": email, "password": password})
        token = body.get("access_token") or body.get("token")
        if not token:
            raise PortalApiError("Connexion PFS echouee: token absent.")
        self.http.set_bearer_token(str(token))
        user = body.get("user") or {}
        user_label = " ".join(
            part for part in (str(user.get("firstName") or user.get("first_name") or "").strip(), str(user.get("lastName") or user.get("last_name") or "").strip()) if part
        )
        self.session = PortalSession(
            source="PFS",
            user_label=user_label or str(user.get("name") or user.get("email") or email),
            expires_at=str(body.get("expires_at") or ""),
            raw={key: value for key, value in body.items() if key not in {"access_token", "token"}},
        )
        return self.session

    def list_orders(
        self,
        page: int = 1,
        per_page: int = 25,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
    ) -> list[PortalOrderSummary]:
        body = self.http.get_json(
            f"{self.api_base_url}/orders/listOrders",
            {
                "page": page,
                "per_page": per_page,
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
            },
        )
        return [normalize_pfs_order_summary(order) for order in body.get("data") or []]

    def get_order(self, order_id: str) -> PortalOrder:
        body = self.http.get_json(f"{self.api_base_url}/orders/{order_id}")
        return normalize_pfs_order_detail(body)


EFASHION_LIST_ORDERS_QUERY = """
query($page: Int, $limit: Int, $filters: CommandeFiltersInput) {
  commandes(page: $page, limit: $limit, filters: $filters) {
    data {
      id_commande
      id_commande_name
      montantTotal
      montantApresRemise
      montantCA
      acheteur { nomSociete }
      dateCreation
      dateCommande
      statut
      commandeStatut { statut_fr }
    }
  }
}
"""


EFASHION_LOGIN_QUERY = """
mutation Login($email: String!, $password: String!, $rememberMe: Boolean!) {
  login(email: $email, password: $password, rememberMe: $rememberMe) {
    user {
      id_vendeur
      email
      nomContact
      nomBoutique
      siret
      tva
      mel
    }
    message
  }
}
"""


EFASHION_ORDER_DETAIL_QUERY = """
query GetOrderDetail($id: Int!) {
  commandeById(id: $id) {
    id_commande
    id_commande_name
    montantTotal
    montantApresRemise
    montantCA
    nb_colis
    statut
    dateCommande
    acheteur { nomSociete nomContact prenomContact email tva_intra data }
    adresseLivraison { adresse codePostal ville telephone mobile NomContact Societe pays { code texte_fr is_cee } }
    adresseFacturation { adresse codePostal ville telephone mobile NomContact Societe pays { code texte_fr is_cee } }
    livraison { libelle telephone email }
    paiement { texte_fr }
    lignes {
      id_ligne
      id_produit
      reference
      reference_base
      quantite_pack
      quantite_total
      prix
      prixReduit
      prixLigne
      couleur_FR
      categorie
      id_categorie
      quantites { q1 q2 q3 q4 q5 q6 q7 q8 q9 q10 q11 q12 }
      declinaisons { d1_FR d2_FR d3_FR d4_FR d5_FR d6_FR d7_FR d8_FR d9_FR d10_FR d11_FR d12_FR }
    }
    remises { libelle typeRemise montant isFraisPort }
  }
}
"""
