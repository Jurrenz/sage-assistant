from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QObject, QSortFilterProxyModel, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyledItemDelegate,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .db import Database
from .excel_import import import_order, import_products
from .injection import launch_ahk_tool, launch_autohotkey, write_injection_queue
from .models import InvoiceLine, Product, SageMapping, build_sage_description, normalize_spaces, utc_now_iso
from .microstore_product_writer import MicrostoreProductPayload, MicrostoreProductWriter, MicrostoreWriteError
from .order_folder import OrderFile, list_order_files
from .portal_orders import EfashionConnector, MicrostoreConnector, PfsConnector, PortalApiError, PortalClient, PortalOrder, PortalOrderLine, PortalOrderSummary, PortalSession
from .product_folder import latest_product_export
from .resolver import Resolver
from .settings import (
    APP_NAME,
    REAL_SAGE_INJECTION_LABEL,
    REAL_SAGE_ONE_LINE_MODE,
    SAGE_50_WINDOW_TITLE,
    default_db_path,
    is_windows,
    load_settings,
    save_settings,
)


STATUS_READY = "Prêt"
STATUS_REVIEW = "À vérifier"
STATUS_INJECTED = "Injecté"
STATUS_DONE = "Traité"
STATUS_ERROR = "Erreur"
TERMINAL_STATUSES = {STATUS_INJECTED, STATUS_DONE}
QUICK_INVOICE_SOURCE = "Facture rapide"

ORDER_SOURCES = ("Toutes", "Microstore", QUICK_INVOICE_SOURCE, "Fichier manuel", "PFS", "eFashion")
ORDER_STATUSES = ("Tous", STATUS_READY, STATUS_REVIEW, STATUS_INJECTED, STATUS_DONE, STATUS_ERROR)
DATE_FILTERS = ("Toutes", "Aujourd'hui", "7 jours", "30 jours")
ROLE_KIND = Qt.UserRole
ROLE_SOURCE = Qt.UserRole + 1
ROLE_KEY = Qt.UserRole + 2
ROLE_PAYLOAD = Qt.UserRole + 3
ROLE_PRODUCT_REF = Qt.UserRole + 4

COMMAND_HEADERS = ["Source", "N commande", "Client", "Date", "Lignes", "Total", "Statut"]
PRODUCT_HEADERS = ["Référence", "Nom", "Catégorie", "Statut", "Prix", "Colisage", "Dernière activité"]
CLIENT_HEADERS = ["Source", "Client", "Société", "Téléphone", "Email", "Ville", "Pays"]
PRODUCT_STATUSES = (
    "Tous",
    "Actif Microstore",
    "Désactivé Microstore",
    "Brouillon",
    "À créer",
    "Modifié localement",
    "Historique local",
)
LINE_HEADERS = [
    "Reference",
    "Categorie",
    "Code Sage",
    "Description",
    "Colisage",
    "Pieces",
    "Prix Sage",
    "Prix Microstore",
    "Statut",
]
MAPPING_HEADERS = ["Categorie fournisseur", "Code Sage", "Actif"]
LINE_COL_REF = 0
LINE_COL_CATEGORY = 1
LINE_COL_CODE = 2
LINE_COL_DESCRIPTION = 3
LINE_COL_PACKAGE_SIZE = 4
LINE_COL_QUANTITY = 5
LINE_COL_PRICE = 6
LINE_COL_CATALOG_PRICE = 7
LINE_COL_STATUS = 8


def _money_label(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f} EUR"


def _decimal_from_text(value: str) -> Decimal | None:
    text = value.strip().replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Prix invalide: {value}") from exc


def _product_status_label(product: Product) -> str:
    if product.workflow_status == "draft":
        return "Brouillon"
    if product.workflow_status == "to_create":
        return "À créer"
    if product.workflow_status == "modified":
        return "Modifié localement"
    if product.workflow_status == "historical":
        return "Historique local"
    if product.microstore_status == "disabled":
        return "Désactivé Microstore"
    if product.microstore_status == "active":
        return "Actif Microstore"
    return "Synchronisé"


def _order_key(order_file: OrderFile | Path) -> str:
    if isinstance(order_file, OrderFile):
        return (order_file.order_number or order_file.path.stem).strip()
    return order_file.stem


def _source_for_path(path: Path, order_folder: str = "") -> str:
    folder = Path(order_folder).expanduser() if order_folder else None
    try:
        if folder and path.resolve().parent == folder.resolve():
            return "Microstore"
    except OSError:
        pass
    return "Microstore" if path.stem.isdigit() else "Fichier manuel"


def _status_from_lines(lines: list[InvoiceLine]) -> str:
    for line in lines:
        line.validate()
    return STATUS_REVIEW if any(line.validation_status != "ok" for line in lines) else STATUS_READY


def _timestamp_from_iso(value: str) -> float:
    if not value:
        return 0
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0


def _display_date(value: str) -> str:
    if not value:
        return ""
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%d/%m/%Y %H:%M")


def _hours_since_iso(value: str) -> float | None:
    timestamp = _timestamp_from_iso(value)
    if not timestamp:
        return None
    return max(0, (datetime.now().timestamp() - timestamp) / 3600)


def should_sync_microstore_products(latest_product_import: str | None, resync_hours: int) -> bool:
    if resync_hours <= 0:
        return True
    if not latest_product_import:
        return True
    hours = _hours_since_iso(latest_product_import)
    return hours is None or hours >= resync_hours


def _product_activity_iso(product: Product) -> str:
    return max(
        product.last_microstore_modified_at or "",
        product.last_local_modified_at or "",
        product.last_seen_at or "",
        product.last_imported_at or "",
    )


def configure_table_columns(
    table: QTableWidget,
    widths: dict[int, int],
    stretch_columns: set[int] | None = None,
) -> None:
    table.setMinimumWidth(0)
    table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(45)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    for column in range(table.columnCount()):
        header.setSectionResizeMode(column, QHeaderView.Interactive)
    for column, width in widths.items():
        if 0 <= column < table.columnCount():
            header.resizeSection(column, width)
    for column in stretch_columns or set():
        if 0 <= column < table.columnCount():
            header.setSectionResizeMode(column, QHeaderView.Stretch)
    table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
    table.verticalHeader().setDefaultSectionSize(28)


def make_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setMinimumWidth(72)
    button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
    return button


def parse_quick_ref_text(text: str) -> tuple[str, int]:
    cleaned = text.strip().upper().replace("×", "X")
    if not cleaned:
        return "", 1
    if "\t" in cleaned:
        cells = [cell.strip() for cell in cleaned.split("\t") if cell.strip()]
        if cells:
            ref = cells[0]
            for cell in cells[1:]:
                qty_text = cell[1:] if cell.startswith("X") else cell
                try:
                    return ref, max(1, int(qty_text))
                except ValueError:
                    continue
            return ref, 1
    parts = cleaned.split()
    if len(parts) >= 2 and parts[-1].startswith("X"):
        try:
            return " ".join(parts[:-1]).strip(), max(1, int(parts[-1][1:]))
        except ValueError:
            return cleaned, 1
    if " X" in cleaned:
        ref, qty = cleaned.rsplit(" X", 1)
        try:
            return ref.strip(), max(1, int(qty.strip()))
        except ValueError:
            return cleaned, 1
    return cleaned, 1


def quick_invoice_to_portal_order(
    lines: list[InvoiceLine],
    order_number: str | None = None,
    created_at: str | None = None,
) -> tuple[PortalOrderSummary, PortalOrder]:
    created = created_at or utc_now_iso()
    number = order_number or f"FR-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    total = sum((line.unit_price_ht or Decimal("0")) * Decimal(line.quantity_pieces or 0) for line in lines)
    raw = {"kind": "quick_invoice"}
    portal_lines = [
        PortalOrderLine(
            ref=line.ref,
            category=line.type_label,
            description=line.description or line.ref,
            package_count=line.package_count or 0,
            package_size=line.package_size,
            quantity_pieces=line.quantity_pieces,
            unit_price_ht=line.unit_price_ht,
            raw={
                "sage_code": line.sage_code,
                "product_id": line.product_id,
                "validation_status": line.validation_status,
                "validation_message": line.validation_message,
            },
        )
        for line in lines
    ]
    summary = PortalOrderSummary(
        source=QUICK_INVOICE_SOURCE,
        order_id=number,
        order_number=number,
        customer="Facture rapide",
        created_at=created,
        status="Brouillon",
        total_amount=total,
        raw=raw,
    )
    detail = PortalOrder(
        source=QUICK_INVOICE_SOURCE,
        order_id=number,
        order_number=number,
        customer="Facture rapide",
        created_at=created,
        status="Brouillon",
        total_amount=total,
        lines=portal_lines,
        raw=raw,
    )
    return summary, detail


QUICK_INVOICE_CLIPBOARD_HEADERS = ["Reference", "Code Sage", "Description", "Paquets", "Colisage", "Pieces", "Prix Sage", "Prix Microstore"]


def quick_invoice_line_to_clipboard_row(line: InvoiceLine) -> list[str]:
    return [
        line.ref,
        line.sage_code,
        line.description,
        str(line.package_count or ""),
        str(line.package_size or ""),
        str(line.quantity_pieces or ""),
        str(line.unit_price_ht or ""),
        str(line.catalog_unit_price_ht or ""),
    ]


def quick_invoice_line_from_clipboard_cells(cells: list[str]) -> InvoiceLine | None:
    if len(cells) < len(QUICK_INVOICE_CLIPBOARD_HEADERS):
        return None
    ref = cells[0].strip().upper()
    if not ref or ref in {"REFERENCE", "RÉFÉRENCE", "REF"}:
        return None
    try:
        package_count = int(cells[3].strip()) if cells[3].strip() else None
        package_size = int(cells[4].strip()) if cells[4].strip() else None
        quantity_pieces = int(cells[5].strip())
        unit_price_ht = _decimal_from_text(cells[6]) if cells[6].strip() else None
        catalog_unit_price_ht = _decimal_from_text(cells[7]) if len(cells) > 7 and cells[7].strip() else None
    except (ValueError, InvalidOperation):
        return None
    line = InvoiceLine(
        ref=ref,
        sage_code=cells[1].strip().upper(),
        description=cells[2].strip() or ref,
        package_count=package_count,
        package_size=package_size,
        quantity_pieces=quantity_pieces,
        unit_price_ht=unit_price_ht,
        catalog_unit_price_ht=catalog_unit_price_ht,
        order_unit_price_ht=unit_price_ht,
        price_confirmed=True,
        source="quick_invoice",
    )
    line.validate()
    return line


class ProductTableModel(QAbstractTableModel):
    def __init__(self, products: list[Product] | None = None) -> None:
        super().__init__()
        self.products = products or []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.products)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(PRODUCT_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(PRODUCT_HEADERS):
            return PRODUCT_HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.products)):
            return None
        product = self.products[index.row()]
        column = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            values = (
                product.ref,
                product.name,
                product.type_label,
                _product_status_label(product),
                str(product.unit_price_ht or ""),
                str(product.package_size or ""),
                _display_date(_product_activity_iso(product)),
            )
            return values[column] if 0 <= column < len(values) else ""
        if role == Qt.UserRole:
            return product.ref
        if role == Qt.UserRole + 1:
            values = (
                product.ref,
                product.name,
                product.type_label,
                _product_status_label(product),
                product.unit_price_ht or Decimal("0"),
                product.package_size or 0,
                _timestamp_from_iso(_product_activity_iso(product)),
            )
            return values[column] if 0 <= column < len(values) else None
        return None

    def set_products(self, products: list[Product]) -> None:
        self.beginResetModel()
        self.products = products
        self.endResetModel()

    def product_at(self, row: int) -> Product | None:
        if 0 <= row < len(self.products):
            return self.products[row]
        return None


class ProductFilterProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.search_text = ""
        self.type_filter = "Tous"
        self.status_filter = "Tous"
        self.setDynamicSortFilter(True)

    def set_filters(self, search: str, type_filter: str, status_filter: str) -> None:
        self.search_text = search.strip().upper()
        self.type_filter = type_filter or "Tous"
        self.status_filter = status_filter or "Tous"
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, ProductTableModel):
            return True
        product = model.product_at(source_row)
        if product is None:
            return False
        if self.type_filter != "Tous" and product.type_label != self.type_filter:
            return False
        if self.status_filter != "Tous" and _product_status_label(product) != self.status_filter:
            return False
        if self.search_text:
            haystack = " ".join([product.ref, product.name, product.type_label, product.remark]).upper()
            if self.search_text not in haystack:
                return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        left_value = left.data(Qt.UserRole + 1)
        right_value = right.data(Qt.UserRole + 1)
        return left_value < right_value


class LineAutocompleteDelegate(QStyledItemDelegate):
    def __init__(self, values: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.values = values

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        completer = QCompleter(self.values, editor)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        editor.setCompleter(completer)
        editor.textEdited.connect(lambda _text: completer.complete())
        QTimer.singleShot(0, completer.complete)
        return editor

    def setEditorData(self, editor, index) -> None:
        if isinstance(editor, QLineEdit):
            editor.setText(str(index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or ""))
            editor.selectAll()
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index) -> None:
        if isinstance(editor, QLineEdit):
            model.setData(index, editor.text(), Qt.EditRole)
            return
        super().setModelData(editor, model, index)


def _order_web_url(source: str, order_id: str, order_number: str = "", microstore_token: str = "") -> str:
    identifier = order_id or order_number
    if source == "eFashion" and identifier:
        return f"https://wholesaler.efashion-paris.com/orderdetails/{identifier}?page=1&limit=25"
    if source == "PFS" and identifier:
        return f"https://wholesaler.parisfashionshops.com/orders/{identifier}/details"
    if source == "Microstore" and identifier and microstore_token:
        return f"https://mc2-h5.dokkr.net/order-detail.html?doc_id={identifier}&lang=fr&key={microstore_token}"
    if source == "Microstore":
        return "https://web.mc.app/#/mc/bill"
    return ""


def _session_from_settings(source: str, payload: dict | None) -> PortalSession | None:
    if not isinstance(payload, dict):
        return None
    return PortalSession(
        source=source,
        user_label=str(payload.get("user_label") or ""),
        expires_at=str(payload.get("expires_at") or ""),
        auth_token=str(payload.get("auth_token") or ""),
        cookies=payload.get("cookies") if isinstance(payload.get("cookies"), list) else [],
        raw=payload.get("raw") if isinstance(payload.get("raw"), dict) else {},
    )


def _session_to_settings(session: PortalSession) -> dict:
    return {
        "user_label": session.user_label,
        "expires_at": session.expires_at,
        "auth_token": session.auth_token,
        "cookies": session.cookies,
        "raw": session.raw,
        "saved_at": utc_now_iso(),
    }


def _raw_get(raw: dict, *keys: str) -> str:
    current = raw
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if current in (None, ""):
        return ""
    if isinstance(current, dict):
        parts = []
        for key in ("Societe", "company_name", "adresse", "address", "codePostal", "zip", "ville", "city", "telephone", "phone", "email"):
            value = current.get(key)
            if value not in (None, ""):
                parts.append(str(value).strip())
        return " - ".join(parts)
    if isinstance(current, list):
        return ", ".join(str(item).strip() for item in current if item not in (None, ""))
    return str(current).strip()


def _order_extra_fields(order: PortalOrder) -> list[tuple[str, str]]:
    raw = order.raw if isinstance(order.raw, dict) else {}
    if order.source == "Microstore":
        client = raw.get("client_info") if isinstance(raw.get("client_info"), dict) else {}
        fields = [
            ("Téléphone", _raw_get(client, "phone") or _raw_get(client, "telephone") or _raw_get(client, "mobile")),
            ("Email", _raw_get(client, "email")),
            ("Adresse livraison", _raw_get(raw, "shipping_address") or _raw_get(client, "address")),
            ("Adresse facturation", _raw_get(raw, "billing_address")),
            ("Paiement", _raw_get(raw, "payment_name") or _raw_get(raw, "pay_name") or _raw_get(raw, "pay_method")),
            ("Transporteur", _raw_get(raw, "express_name") or _raw_get(raw, "shipping_name")),
            ("Dernier traitement", _display_date(_raw_get(raw, "utime") or _raw_get(raw, "updated_at"))),
        ]
    elif order.source == "PFS":
        customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
        fields = [
            ("Boutique", _raw_get(customer, "shop") or _raw_get(customer, "name")),
            ("SIRET", _raw_get(customer, "siret")),
            ("TVA", _raw_get(customer, "vat") or _raw_get(customer, "vat_number")),
            ("EORI", _raw_get(customer, "eori")),
            ("Téléphone", _raw_get(customer, "phone") or _raw_get(customer, "telephone")),
            ("Paiement", _raw_get(raw, "payment") or _raw_get(raw, "payment_method")),
            ("Transporteur", _raw_get(raw, "carrier") or _raw_get(raw, "shipping_method")),
            ("Adresse livraison", _raw_get(raw, "shipping_address") or _raw_get(raw, "delivery_address")),
            ("Adresse facturation", _raw_get(raw, "billing_address")),
            ("État commande", _raw_get(raw, "status")),
        ]
    elif order.source == "eFashion":
        fields = [
            ("Email client", _raw_get(raw, "acheteur", "email")),
            ("TVA", _raw_get(raw, "acheteur", "tva_intra")),
            ("Livraison", _raw_get(raw, "adresseLivraison")),
            ("Facturation", _raw_get(raw, "adresseFacturation")),
            ("Transporteur", _raw_get(raw, "livraison", "libelle")),
            ("Service", _raw_get(raw, "livraison", "email") or _raw_get(raw, "livraison", "telephone")),
            ("Paiement", _raw_get(raw, "paiement", "texte_fr")),
            ("Nombre colis", _raw_get(raw, "nb_colis")),
            ("Promotions/remises", _raw_get(raw, "remises")),
        ]
    else:
        fields = []
    return [(label, value) for label, value in fields if value]


def line_headers_for_source(source: str = "") -> list[str]:
    headers = list(LINE_HEADERS)
    if source == "eFashion":
        headers[LINE_COL_PRICE] = "Prix commande"
    elif source == "PFS":
        headers[LINE_COL_PRICE] = "Prix commande"
    elif source == "Microstore":
        headers[LINE_COL_PRICE] = "Prix Sage"
    elif source == QUICK_INVOICE_SOURCE:
        headers[LINE_COL_PRICE] = "Prix Sage"
    return headers


def _mappings_by_type(db: Database) -> dict[str, SageMapping]:
    return {mapping.microstore_type: mapping for mapping in db.list_mappings()}


def _mappings_by_code(db: Database) -> dict[str, list[SageMapping]]:
    result: dict[str, list[SageMapping]] = {}
    for mapping in db.list_mappings():
        result.setdefault(mapping.sage_code.upper(), []).append(mapping)
    return result


def _description_for_line(line: InvoiceLine, mapping: SageMapping | None = None) -> str:
    return build_sage_description(line.ref, mapping.sage_label if mapping else "", line.type_label)


def _line_price_editable(source: str) -> bool:
    return source in {"", "manual", "Microstore", QUICK_INVOICE_SOURCE}


def lines_with_saved_order_edits(db: Database, source: str, key: str, fallback_lines: list[InvoiceLine]) -> list[InvoiceLine]:
    return db.get_order_line_edits(source, key) or fallback_lines


def _apply_mapping_to_line(line: InvoiceLine, mapping: SageMapping | None) -> None:
    if mapping is None:
        return
    line.type_label = mapping.microstore_type
    line.sage_code = mapping.sage_code
    line.description = _description_for_line(line, mapping)
    line.validate()


def _update_line_table_row(table: QTableWidget, row: int, line: InvoiceLine) -> None:
    table.blockSignals(True)
    values = {
        LINE_COL_CATEGORY: line.type_label,
        LINE_COL_CODE: line.sage_code,
        LINE_COL_DESCRIPTION: line.description,
        LINE_COL_PACKAGE_SIZE: str(line.package_size or ""),
        LINE_COL_QUANTITY: str(line.quantity_pieces),
        LINE_COL_PRICE: str(line.unit_price_ht or ""),
        LINE_COL_CATALOG_PRICE: str(line.catalog_unit_price_ht or ""),
        LINE_COL_STATUS: line.validation_status if line.validation_status == "ok" else line.validation_message,
    }
    for col, value in values.items():
        item = table.item(row, col)
        if item:
            item.setText(value)
    table.blockSignals(False)


def populate_lines_table(table: QTableWidget, lines: list[InvoiceLine], editable: bool = False, source: str = "") -> None:
    table.blockSignals(True)
    table.setSortingEnabled(False)
    table.setRowCount(len(lines))
    for row, line in enumerate(lines):
        status = line.validation_status if line.validation_status == "ok" else line.validation_message
        values = [
            line.ref,
            line.type_label,
            line.sage_code,
            line.description,
            str(line.package_size or ""),
            str(line.quantity_pieces),
            str(line.unit_price_ht or ""),
            str(line.catalog_unit_price_ht or ""),
            status,
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            editable_columns = {LINE_COL_CATEGORY, LINE_COL_CODE, LINE_COL_DESCRIPTION, LINE_COL_QUANTITY}
            if _line_price_editable(source):
                editable_columns.add(LINE_COL_PRICE)
            if not editable or col not in editable_columns:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if col == LINE_COL_PRICE:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            table.setItem(row, col, item)
    table.blockSignals(False)


def configure_line_table_autocomplete(table: QTableWidget, db: Database) -> None:
    categories = sorted(_mappings_by_type(db))
    codes = sorted(_mappings_by_code(db))
    table.setItemDelegateForColumn(LINE_COL_CATEGORY, LineAutocompleteDelegate(categories, table))
    table.setItemDelegateForColumn(LINE_COL_CODE, LineAutocompleteDelegate(codes, table))


def apply_line_table_item_change(
    table: QTableWidget,
    lines: list[InvoiceLine],
    db: Database,
    source: str,
    item: QTableWidgetItem,
    message_callback=None,
) -> None:
    row = item.row()
    if row >= len(lines):
        return
    mappings_by_type = _mappings_by_type(db)
    mappings_by_code = _mappings_by_code(db)
    line = lines[row]
    try:
        if item.column() == LINE_COL_CATEGORY:
            category = item.text().strip()
            line.type_label = category
            _apply_mapping_to_line(line, mappings_by_type.get(category))
        elif item.column() == LINE_COL_CODE:
            code = item.text().strip().upper()
            line.sage_code = code
            matches = mappings_by_code.get(code, [])
            if len(matches) == 1:
                _apply_mapping_to_line(line, matches[0])
            elif len(matches) > 1:
                mapping = mappings_by_type.get(line.type_label)
                if mapping and mapping.sage_code == code:
                    line.description = _description_for_line(line, mapping)
                if message_callback:
                    message_callback(f"Code {code} correspond a plusieurs categories. Categorie conservee.")
                line.validate()
            else:
                line.validate()
        elif item.column() == LINE_COL_DESCRIPTION:
            line.description = normalize_spaces(item.text())
            line.validate()
        elif item.column() == LINE_COL_PACKAGE_SIZE:
            package_size_text = item.text().strip()
            line.package_size = int(package_size_text) if package_size_text else None
            line.validate()
        elif item.column() == LINE_COL_QUANTITY:
            line.quantity_pieces = int(item.text().strip())
            line.validate()
        elif item.column() == LINE_COL_PRICE and _line_price_editable(source):
            price_text = item.text().strip().replace(",", ".")
            line.unit_price_ht = Decimal(price_text) if price_text else None
            line.order_unit_price_ht = line.unit_price_ht
            line.price_confirmed = True
            line.validate()
    except Exception as exc:
        line.validation_status = "blocked"
        line.validation_message = f"valeur invalide: {exc}"
    _update_line_table_row(table, row, line)


class OrderDetailDialog(QDialog):
    def __init__(
        self,
        lines: list[InvoiceLine],
        summary: dict[str, str],
        db: Database,
        autosave_callback: Callable[[list[InvoiceLine]], None] | None = None,
        reset_callback: Callable[[], list[InvoiceLine]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.lines = list(lines)
        self.db = db
        self.autosave_callback = autosave_callback
        self.reset_callback = reset_callback
        self._autosave_enabled = True
        self.inject_requested = False
        self.source = summary.get("source", "")
        self.web_url = summary.get("web_url", "")
        self.setWindowTitle(f"Détail commande {summary.get('number', '')}".strip())
        self.resize(1000, 620)

        layout = QVBoxLayout(self)
        self.status_label = QLabel(summary.get("status", ""))
        summary_text = " | ".join(
            part
            for part in (
                summary.get("source", ""),
                summary.get("number", ""),
                summary.get("customer", ""),
                summary.get("date", ""),
                summary.get("total", ""),
                self.status_label.text(),
            )
            if part
        )
        self.summary_label = QLabel(summary_text)
        self.summary_label.setWordWrap(True)
        self.summary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.summary_label)

        extra_fields = summary.get("extra_fields")
        if isinstance(extra_fields, list) and extra_fields:
            self.extra_info = QTextEdit()
            self.extra_info.setReadOnly(True)
            self.extra_info.setMaximumHeight(92)
            self.extra_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.extra_info.setPlainText("\n".join(f"{label}: {value}" for label, value in extra_fields[:10]))
            layout.addWidget(self.extra_info)

        self.message_label = QLabel("")
        layout.addWidget(self.message_label)

        self.quick_add_toggle = make_button("Ajouter une référence ▸")
        self.quick_add_toggle.setCheckable(True)
        self.quick_add_toggle.toggled.connect(self._toggle_quick_add)
        layout.addWidget(self.quick_add_toggle)

        self.quick_add_widget = QWidget()
        quick_add_layout = QVBoxLayout(self.quick_add_widget)
        quick_add_layout.setContentsMargins(0, 0, 0, 0)
        entry = QHBoxLayout()
        self.ref_input = QLineEdit()
        self.ref_input.setPlaceholderText("Ajouter une référence, ex: FL530-1 ou FL530-1 x2")
        self.ref_input.textChanged.connect(self._refresh_suggestions)
        self.ref_input.returnPressed.connect(self._add_line)
        add_button = make_button("Ajouter")
        add_button.clicked.connect(self._add_line)
        entry.addWidget(self.ref_input, 1)
        entry.addWidget(add_button)
        quick_add_layout.addLayout(entry)

        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(100)
        self.suggestions.itemDoubleClicked.connect(lambda _item: self._add_line())
        quick_add_layout.addWidget(self.suggestions)
        self.quick_add_widget.setVisible(False)
        layout.addWidget(self.quick_add_widget)

        self.table = QTableWidget(0, len(LINE_HEADERS))
        self.table.setHorizontalHeaderLabels(line_headers_for_source(self.source))
        configure_table_columns(
            self.table,
            {
                LINE_COL_REF: 90,
                LINE_COL_CATEGORY: 150,
                LINE_COL_CODE: 70,
                LINE_COL_PACKAGE_SIZE: 75,
                LINE_COL_QUANTITY: 70,
                LINE_COL_PRICE: 80,
                LINE_COL_CATALOG_PRICE: 95,
                LINE_COL_STATUS: 130,
            },
            {LINE_COL_DESCRIPTION},
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.installEventFilter(self)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        actions = QGridLayout()
        remove_button = make_button("Supprimer")
        remove_button.setToolTip("Supprimer ligne")
        remove_button.clicked.connect(self._remove_selected_lines)
        reset_button = make_button("Retour origine")
        reset_button.setToolTip("Annuler les corrections et revenir à la commande source")
        reset_button.clicked.connect(self._reset_to_original)
        reset_button.setEnabled(self.reset_callback is not None)
        inject_button = make_button("Injecter")
        inject_button.setToolTip("Injecter dans Sage")
        inject_button.clicked.connect(self._accept_for_injection)
        open_web_button = make_button("Site")
        open_web_button.setToolTip("Ouvrir sur le site")
        open_web_button.clicked.connect(self._open_web_page)
        open_web_button.setEnabled(bool(self.web_url))
        copy_link_button = make_button("Copier lien")
        copy_link_button.clicked.connect(self._copy_web_link)
        copy_link_button.setEnabled(bool(self.web_url))
        close_button = make_button("Fermer")
        close_button.clicked.connect(self.accept)
        for index, button in enumerate((remove_button, reset_button, open_web_button, copy_link_button, inject_button, close_button)):
            actions.addWidget(button, index // 3, index % 3)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        self.ref_input.installEventFilter(self)
        self.suggestions.installEventFilter(self)
        self._validate_lines()
        self._refresh_table()
        self._refresh_status()

    def _refresh_table(self) -> None:
        self._autosave_enabled = False
        populate_lines_table(self.table, self.lines, editable=True, source=self.source)
        configure_line_table_autocomplete(self.table, self.db)
        self._autosave_enabled = True

    def _toggle_quick_add(self, checked: bool) -> None:
        self.quick_add_widget.setVisible(checked)
        self.quick_add_toggle.setText("Masquer ajout rapide ▾" if checked else "Ajouter une référence ▸")
        if checked:
            QTimer.singleShot(0, self.ref_input.setFocus)

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self.ref_input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Down and self.suggestions.count():
                self.suggestions.setFocus()
                self.suggestions.setCurrentRow(0)
                return True
        if watched is self.suggestions and event.type() == QEvent.KeyPress:
            if event.key() in {Qt.Key_Return, Qt.Key_Enter}:
                self._add_line()
                return True
            if event.key() == Qt.Key_Escape:
                self.ref_input.setFocus()
                return True
        if watched is self.table and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space:
            item = self.table.currentItem()
            if item and item.flags() & Qt.ItemIsEditable:
                self.table.editItem(item)
                return True
        return super().eventFilter(watched, event)

    def _refresh_suggestions(self) -> None:
        ref, _packages = parse_quick_ref_text(self.ref_input.text())
        self.suggestions.clear()
        if len(ref) < 2:
            return
        for product in self.db.search_products(ref, limit=8):
            label = f"{product.ref} | {product.name or product.type_label} | {product.unit_price_ht or ''} EUR | colisage {product.package_size or '?'}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, product.ref)
            self.suggestions.addItem(item)

    def _selected_ref(self) -> str:
        selected = self.suggestions.selectedItems()
        if selected:
            return str(selected[0].data(Qt.UserRole) or "")
        ref, _packages = parse_quick_ref_text(self.ref_input.text())
        return ref

    def _add_line(self) -> None:
        text = self.ref_input.text()
        if self._add_lines_from_text(text):
            self.ref_input.clear()
            self.suggestions.clear()
            self.ref_input.setFocus()
            return
        pasted_line = self._line_from_pasted_text(text)
        if pasted_line is not None:
            self.lines.append(pasted_line)
            self._refresh_table()
            self._refresh_status()
            self._autosave("ligne ajoutée")
            self.ref_input.clear()
            self.suggestions.clear()
            self.message_label.setText(f"{pasted_line.ref} ajouté depuis une ligne copiée.")
            self.ref_input.setFocus()
            return
        typed_ref, packages = parse_quick_ref_text(text)
        ref = self._selected_ref() or typed_ref
        self._add_line_by_ref(ref, packages)

    def _add_lines_from_text(self, text: str) -> bool:
        entries = [line.strip() for line in text.replace(";", "\n").splitlines() if line.strip()]
        if len(entries) <= 1:
            return False
        added = 0
        errors: list[str] = []
        for entry_text in entries:
            pasted_line = self._line_from_pasted_text(entry_text)
            if pasted_line is not None:
                self.lines.append(pasted_line)
                added += 1
                continue
            if self._is_quick_invoice_clipboard_header(entry_text):
                continue
            ref, packages = parse_quick_ref_text(entry_text)
            if self._add_line_by_ref(ref, packages, refresh=False, clear_input=False):
                added += 1
            else:
                errors.append(ref or entry_text)
        self._refresh_table()
        self._refresh_status()
        self._autosave(f"{added} ligne(s) ajoutée(s)")
        if errors:
            self.message_label.setText(f"{added} ligne(s) ajoutée(s). Références inconnues: {', '.join(errors[:6])}")
        else:
            self.message_label.setText(f"{added} ligne(s) ajoutée(s).")
        return True

    def _is_quick_invoice_clipboard_header(self, text: str) -> bool:
        cells = [cell.strip().upper() for cell in text.split("\t")]
        return len(cells) >= 3 and cells[0] in {"REFERENCE", "RÉFÉRENCE", "REF"} and "CODE SAGE" in cells[1]

    def _line_from_pasted_text(self, text: str) -> InvoiceLine | None:
        if "\t" not in text:
            return None
        line = quick_invoice_line_from_clipboard_cells([cell.strip() for cell in text.split("\t")])
        if line is None:
            return None
        product = self.db.get_product_by_ref(line.ref)
        if product is not None:
            line.product_id = product.id
            line.type_label = product.type_label
            line.catalog_unit_price_ht = product.unit_price_ht
        line.source = self.source or "manual"
        line.validate()
        return line

    def _add_line_by_ref(self, ref: str, packages: int, refresh: bool = True, clear_input: bool = True) -> bool:
        if not ref:
            self.message_label.setText("Tape une référence.")
            return False
        product = self.db.get_product_by_ref(ref)
        if product is None:
            self.message_label.setText(f"Référence inconnue: {ref}")
            return False
        package_size = product.package_size or 0
        quantity_pieces = packages * package_size if package_size else packages
        line = Resolver(self.db).line_from_product(product, quantity_pieces=quantity_pieces, package_count=packages, source=self.source or "manual")
        self.lines.append(line)
        if refresh:
            self._refresh_table()
            self._refresh_status()
            self._autosave("ligne ajoutée")
        if clear_input:
            self.ref_input.clear()
            self.suggestions.clear()
            self.ref_input.setFocus()
        self.message_label.setText(f"{line.ref} ajouté: {packages} paquet(s), {line.quantity_pieces} pièce(s).")
        return True

    def _accept_for_injection(self) -> None:
        self._save_corrections(show_message=False)
        self.inject_requested = True
        self.accept()

    def _open_web_page(self) -> None:
        if self.web_url:
            QDesktopServices.openUrl(QUrl(self.web_url))

    def _copy_web_link(self) -> None:
        if self.web_url:
            QApplication.clipboard().setText(self.web_url)
            self.message_label.setText("Lien copie dans le presse-papiers.")

    def _remove_selected_lines(self) -> None:
        rows = {item.row() for item in self.table.selectedItems()}
        rows.update(index.row() for index in self.table.selectionModel().selectedRows())
        current_row = self.table.currentRow()
        if current_row >= 0:
            rows.add(current_row)
        rows = sorted(rows, reverse=True)
        for row in rows:
            if row < len(self.lines):
                del self.lines[row]
        self._refresh_table()
        self._refresh_status()
        if rows:
            self._autosave("ligne supprimée")

    def _save_corrections(self, show_message: bool = True) -> None:
        self._validate_lines()
        self._refresh_table()
        self._refresh_status()
        self._autosave("corrections sauvegardées")
        if show_message:
            self.message_label.setText("Corrections sauvegardées automatiquement.")

    def _autosave(self, reason: str = "") -> None:
        if not self._autosave_enabled or self.autosave_callback is None:
            return
        self._validate_lines()
        try:
            self.autosave_callback(self.lines)
        except Exception as exc:
            self.message_label.setText(f"Sauvegarde auto impossible: {exc}")
            return
        if reason:
            self.message_label.setText(f"{reason}. Sauvegardé automatiquement.")

    def _reset_to_original(self) -> None:
        if self.reset_callback is None:
            return
        if QMessageBox.question(self, APP_NAME, "Revenir à la commande d'origine et supprimer toutes les corrections ?") != QMessageBox.Yes:
            return
        try:
            self.lines = self.reset_callback()
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Retour origine impossible: {exc}")
            return
        self._validate_lines()
        self._refresh_table()
        self._refresh_status()
        self.message_label.setText("Commande d'origine restaurée.")

    def _validate_lines(self) -> None:
        for line in self.lines:
            line.validate()

    def _refresh_status(self) -> None:
        status = _status_from_lines(self.lines)
        self.status_label.setText(status)
        if hasattr(self, "summary_label"):
            parts = self.summary_label.text().split(" | ")
            if parts:
                parts[-1] = status
                self.summary_label.setText(" | ".join(parts))
        blocked = [line for line in self.lines if line.validation_status != "ok"]
        if blocked:
            self.message_label.setText("Lignes a verifier: " + ", ".join(line.ref for line in blocked[:8]))
        elif not self.message_label.text():
            self.message_label.setText("Toutes les lignes sont pretes.")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        apply_line_table_item_change(self.table, self.lines, self.db, self.source, item, self.message_label.setText)
        self._autosave("correction appliquée")


class QuickInvoiceDialog(QDialog):
    def __init__(self, db: Database, resolver: Resolver, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.resolver = resolver
        self.lines: list[InvoiceLine] = []
        self.inject_requested = False
        self.setWindowTitle("Facture rapide")
        self.resize(900, 540)

        layout = QVBoxLayout(self)
        entry = QHBoxLayout()
        self.ref_input = QLineEdit()
        self.ref_input.setPlaceholderText("Référence ou référence x paquets, ex: FL530-1 ou FL530-1 x2")
        self.ref_input.textChanged.connect(self._refresh_suggestions)
        self.ref_input.returnPressed.connect(self._add_line)
        add_button = make_button("Ajouter")
        add_button.clicked.connect(self._add_line)
        entry.addWidget(self.ref_input, 1)
        entry.addWidget(add_button)
        layout.addLayout(entry)

        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(110)
        self.suggestions.itemDoubleClicked.connect(lambda _item: self._add_line())
        layout.addWidget(self.suggestions)

        self.table = QTableWidget(0, len(LINE_HEADERS))
        self.table.setHorizontalHeaderLabels(line_headers_for_source(QUICK_INVOICE_SOURCE))
        configure_table_columns(
            self.table,
            {
                LINE_COL_REF: 90,
                LINE_COL_CATEGORY: 150,
                LINE_COL_CODE: 70,
                LINE_COL_PACKAGE_SIZE: 75,
                LINE_COL_QUANTITY: 70,
                LINE_COL_PRICE: 80,
                LINE_COL_CATALOG_PRICE: 95,
                LINE_COL_STATUS: 130,
            },
            {LINE_COL_DESCRIPTION},
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.installEventFilter(self)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        actions = QGridLayout()
        self.status = QLabel("Ajoute une référence pour préparer une facture rapide.")
        self.status.setWordWrap(True)
        remove_button = make_button("Supprimer")
        remove_button.setToolTip("Supprimer ligne")
        remove_button.clicked.connect(self._remove_selected_lines)
        copy_button = make_button("Copier liste")
        copy_button.clicked.connect(self._copy_lines)
        clear_button = make_button("Vider")
        clear_button.clicked.connect(self._clear)
        inject_button = make_button("Injecter")
        inject_button.setToolTip("Injecter dans Sage")
        inject_button.clicked.connect(self._accept_for_injection)
        close_button = make_button("Fermer")
        close_button.clicked.connect(self.accept)
        actions.addWidget(self.status, 0, 0, 1, 3)
        for index, button in enumerate((remove_button, copy_button, clear_button, inject_button, close_button)):
            actions.addWidget(button, 1 + index // 3, index % 3)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        self.ref_input.installEventFilter(self)
        self.suggestions.installEventFilter(self)
        QTimer.singleShot(0, self.ref_input.setFocus)

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self.ref_input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Down and self.suggestions.count():
                self.suggestions.setFocus()
                self.suggestions.setCurrentRow(0)
                return True
        if watched is self.suggestions and event.type() == QEvent.KeyPress:
            if event.key() in {Qt.Key_Return, Qt.Key_Enter}:
                self._add_line()
                return True
            if event.key() == Qt.Key_Escape:
                self.ref_input.setFocus()
                return True
        if watched is self.table and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Space:
            item = self.table.currentItem()
            if item and item.flags() & Qt.ItemIsEditable:
                self.table.editItem(item)
                return True
        return super().eventFilter(watched, event)

    def _refresh_suggestions(self) -> None:
        ref, _packages = parse_quick_ref_text(self.ref_input.text())
        self.suggestions.clear()
        if len(ref) < 2:
            return
        for product in self.db.search_products(ref, limit=8):
            label = f"{product.ref} | {product.name or product.type_label} | {product.unit_price_ht or ''} EUR | colisage {product.package_size or '?'}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, product.ref)
            self.suggestions.addItem(item)

    def _selected_ref(self) -> str:
        selected = self.suggestions.selectedItems()
        if selected:
            return str(selected[0].data(Qt.UserRole) or "")
        ref, _packages = parse_quick_ref_text(self.ref_input.text())
        return ref

    def _add_line(self) -> None:
        text = self.ref_input.text()
        if self._add_lines_from_text(text):
            self.ref_input.clear()
            self.suggestions.clear()
            self.ref_input.setFocus()
            return
        pasted_line = self._line_from_pasted_text(text)
        if pasted_line is not None:
            self.lines.append(pasted_line)
            self._refresh_table()
            self.ref_input.clear()
            self.suggestions.clear()
            self.status.setText(f"{pasted_line.ref} ajouté depuis une ligne copiée.")
            self.ref_input.setFocus()
            return
        typed_ref, packages = parse_quick_ref_text(text)
        ref = self._selected_ref() or typed_ref
        self._add_line_by_ref(ref, packages)

    def _add_lines_from_text(self, text: str) -> bool:
        entries = [line.strip() for line in text.replace(";", "\n").splitlines() if line.strip()]
        if len(entries) <= 1:
            return False
        added = 0
        errors: list[str] = []
        for entry in entries:
            pasted_line = self._line_from_pasted_text(entry)
            if pasted_line is not None:
                self.lines.append(pasted_line)
                added += 1
                continue
            if self._is_quick_invoice_clipboard_header(entry):
                continue
            ref, packages = parse_quick_ref_text(entry)
            if self._add_line_by_ref(ref, packages, refresh=False, clear_input=False):
                added += 1
            else:
                errors.append(ref or entry)
        self._refresh_table()
        if errors:
            self.status.setText(f"{added} ligne(s) ajoutée(s). Références inconnues: {', '.join(errors[:6])}")
        else:
            self.status.setText(f"{added} ligne(s) ajoutée(s).")
        return True

    def _is_quick_invoice_clipboard_header(self, text: str) -> bool:
        cells = [cell.strip().upper() for cell in text.split("\t")]
        return len(cells) >= 3 and cells[0] in {"REFERENCE", "RÉFÉRENCE", "REF"} and "CODE SAGE" in cells[1]

    def _line_from_pasted_text(self, text: str) -> InvoiceLine | None:
        if "\t" not in text:
            return None
        line = quick_invoice_line_from_clipboard_cells([cell.strip() for cell in text.split("\t")])
        if line is None:
            return None
        product = self.db.get_product_by_ref(line.ref)
        if product is not None:
            line.product_id = product.id
            line.type_label = product.type_label
            line.catalog_unit_price_ht = product.unit_price_ht
        line.validate()
        return line

    def _add_line_by_ref(self, ref: str, packages: int, refresh: bool = True, clear_input: bool = True) -> bool:
        if not ref:
            self.status.setText("Tape une référence.")
            return False
        product = self.db.get_product_by_ref(ref)
        if product is None:
            self.status.setText(f"Référence inconnue: {ref}")
            return False
        package_size = product.package_size or 0
        quantity_pieces = packages * package_size if package_size else packages
        line = self.resolver.line_from_product(product, quantity_pieces=quantity_pieces, package_count=packages, source="quick_invoice")
        self.lines.append(line)
        if refresh:
            self._refresh_table()
        if clear_input:
            self.ref_input.clear()
            self.suggestions.clear()
            self.ref_input.setFocus()
        self.status.setText(f"{line.ref} ajouté: {packages} paquet(s), {line.quantity_pieces} pièce(s).")
        return True

    def _refresh_table(self) -> None:
        populate_lines_table(self.table, self.lines, editable=True, source=QUICK_INVOICE_SOURCE)
        configure_line_table_autocomplete(self.table, self.db)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        apply_line_table_item_change(self.table, self.lines, self.db, QUICK_INVOICE_SOURCE, item, self.status.setText)
        self.status.setText(_status_from_lines(self.lines) if self.lines else "Aucune ligne.")

    def _remove_selected_lines(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        for row in rows:
            if row < len(self.lines):
                del self.lines[row]
        self._refresh_table()
        self.status.setText(f"{len(self.lines)} ligne(s) dans la facture rapide.")

    def _clear(self) -> None:
        self.lines.clear()
        self._refresh_table()
        self.status.setText("Facture rapide vidée.")

    def _copy_lines(self) -> None:
        if not self.lines:
            self.status.setText("Aucune ligne à copier.")
            return
        selected_rows = sorted({item.row() for item in self.table.selectedItems()})
        rows_to_copy = selected_rows if selected_rows else list(range(len(self.lines)))
        rows = ["\t".join(QUICK_INVOICE_CLIPBOARD_HEADERS)]
        for row in rows_to_copy:
            if row < len(self.lines):
                rows.append("\t".join(quick_invoice_line_to_clipboard_row(self.lines[row])))
        QApplication.clipboard().setText("\n".join(rows))
        self.status.setText(f"{len(rows_to_copy)} ligne(s) copiée(s) avec détails.")

    def _accept_for_injection(self) -> None:
        if not self.lines:
            self.status.setText("Aucune ligne à injecter.")
            return
        self.inject_requested = True
        self.accept()


class InjectionControlDialog(QDialog):
    def __init__(self, process: subprocess.Popen[str], control_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.process = process
        self.control_path = control_path
        self.setWindowTitle("Injection Sage en cours")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setModal(False)
        self.resize(340, 150)

        layout = QVBoxLayout(self)
        self.status = QLabel("Injection en cours. Ne touche pas à Sage pendant l'envoi.")
        self.status.setWordWrap(True)
        shortcuts = QLabel("Raccourcis: Ctrl+Alt+P pause/reprise | Ctrl+Alt+S stop")
        shortcuts.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(shortcuts)

        actions = QHBoxLayout()
        self.pause_button = make_button("Pause")
        self.resume_button = make_button("Reprendre")
        self.stop_button = make_button("Stop")
        self.pause_button.clicked.connect(self.pause)
        self.resume_button.clicked.connect(self.resume)
        self.stop_button.clicked.connect(self.stop)
        actions.addWidget(self.pause_button)
        actions.addWidget(self.resume_button)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._poll_process)
        self.timer.start()
        self._write_control("running")

    def pause(self) -> None:
        self._write_control("paused")
        self.status.setText("Injection en pause. Clique Reprendre ou utilise Ctrl+Alt+P.")

    def resume(self) -> None:
        self._write_control("running")
        self.status.setText("Injection reprise. Ne touche pas à Sage pendant l'envoi.")

    def stop(self) -> None:
        self._write_control("stop")
        self.status.setText("Arrêt demandé. Fin de l'étape en cours...")
        self.stop_button.setEnabled(False)

    def _write_control(self, value: str) -> None:
        try:
            self.control_path.write_text(value, encoding="utf-8")
        except OSError as exc:
            self.status.setText(f"Contrôle injection impossible: {exc}")

    def _poll_process(self) -> None:
        return_code = self.process.poll()
        if return_code is None:
            return
        self.timer.stop()
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        if return_code == 0:
            self.status.setText("Injection terminée. Vérifie visuellement Sage.")
        elif return_code == 2:
            self.status.setText("Injection arrêtée.")
        else:
            self.status.setText(f"Injection terminée avec erreur ({return_code}).")
        QTimer.singleShot(2500, self.accept)

    def closeEvent(self, event) -> None:
        if self.process.poll() is None:
            self.stop()
        super().closeEvent(event)


class SageMappingsDialog(QDialog):
    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Mappings Sage")
        self.resize(820, 620)

        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        self.mapping_type = QLineEdit()
        self.mapping_type.setPlaceholderText("Categorie fournisseur")
        self.mapping_code = QLineEdit()
        self.mapping_code.setPlaceholderText("Code Sage")
        form.addWidget(self.mapping_type, 3)
        form.addWidget(self.mapping_code, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        new_button = make_button("Nouveau")
        new_button.clicked.connect(self._clear_form)
        save_button = make_button("Ajouter / modifier")
        save_button.clicked.connect(self._save_mapping)
        disable_button = make_button("Desactiver")
        disable_button.clicked.connect(self._disable_mapping)
        restore_button = make_button("Restaurer defauts")
        restore_button.clicked.connect(self._restore_defaults)
        close_button = make_button("Fermer")
        close_button.clicked.connect(self.accept)
        actions.addWidget(new_button)
        actions.addWidget(save_button)
        actions.addWidget(disable_button)
        actions.addStretch(1)
        actions.addWidget(restore_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self.table = QTableWidget(0, len(MAPPING_HEADERS))
        self.table.setHorizontalHeaderLabels(MAPPING_HEADERS)
        configure_table_columns(self.table, {0: 360, 1: 90, 2: 70}, {0})
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._load_selected_mapping)
        layout.addWidget(self.table, 1)
        self._refresh()

    def _refresh(self) -> None:
        mappings = self.db.list_mappings(active_only=False)
        self.table.setRowCount(len(mappings))
        for row, mapping in enumerate(mappings):
            values = [mapping.microstore_type, mapping.sage_code, "Oui" if mapping.is_active else "Non"]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def _clear_form(self) -> None:
        self.table.clearSelection()
        self.mapping_type.clear()
        self.mapping_code.clear()

    def _save_mapping(self) -> None:
        mapping = SageMapping(
            microstore_type=self.mapping_type.text().strip(),
            sage_code=self.mapping_code.text().strip(),
            sage_label=self.mapping_code.text().strip().upper(),
        )
        if not mapping.microstore_type or not mapping.sage_code:
            QMessageBox.warning(self, APP_NAME, "Categorie et code Sage sont obligatoires.")
            return
        self.db.upsert_mapping(mapping)
        self._refresh()

    def _disable_mapping(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        if not rows:
            QMessageBox.information(self, APP_NAME, "Selectionne un mapping a desactiver.")
            return
        self.db.deactivate_mapping(self.table.item(rows[0], 0).text())
        self._clear_form()
        self._refresh()

    def _restore_defaults(self) -> None:
        restored = self.db.restore_default_mappings()
        self._refresh()
        QMessageBox.information(self, APP_NAME, f"{restored} mapping(s) par defaut restaure(s).")

    def _load_selected_mapping(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        if not rows:
            return
        row = rows[0]
        self.mapping_type.setText(self.table.item(row, 0).text())
        self.mapping_code.setText(self.table.item(row, 1).text())


class ProductDraftDialog(QDialog):
    def __init__(self, product: Product | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.product = product
        self.saved_product: Product | None = None
        self.setWindowTitle("Fiche produit")
        self.resize(760, 640)

        layout = QVBoxLayout(self)
        self.ref_input = QLineEdit(product.ref if product else "")
        self.type_input = QLineEdit(product.type_label if product else "")
        self.name_input = QLineEdit(product.name if product else "")
        self.price_input = QLineEdit(str(product.unit_price_ht or "") if product else "")
        self.package_input = QSpinBox()
        self.package_input.setRange(0, 10000)
        self.package_input.setSpecialValueText("vide")
        self.package_input.setValue((product.package_size or 0) if product else 0)
        self.content_input = QLineEdit(product.content_label if product else "")
        self.composition_input = QLineEdit(product.composition if product else "")
        self.color_input = QLineEdit(product.color if product else "MIX")
        self.stock_input = QSpinBox()
        self.stock_input.setRange(-1, 10_000_000)
        self.stock_input.setSpecialValueText("vide")
        self.stock_input.setValue(product.stock_snapshot if product and product.stock_snapshot is not None else -1)
        self.brand_input = QLineEdit(product.brand if product else "")
        self.year_input = QLineEdit(product.year if product else "")
        self.season_input = QLineEdit(product.season if product else "")
        self.pieces_outside_package_input = QSpinBox()
        self.pieces_outside_package_input.setRange(0, 10000)
        self.pieces_outside_package_input.setSpecialValueText("vide")
        self.pieces_outside_package_input.setValue((product.pieces_outside_package or 0) if product else 0)
        self.weight_input = QSpinBox()
        self.weight_input.setRange(0, 1_000_000)
        self.weight_input.setSpecialValueText("vide")
        self.weight_input.setValue((product.weight_grams or 0) if product else 0)
        self.origin_input = QLineEdit(product.origin_country if product else "")
        self.created_at_input = QLineEdit(_display_date(product.created_at or "") if product else "")
        self.promo_input = QLineEdit(product.promo if product else "")
        self.discount_input = QLineEdit(str(product.discount_percent or "") if product else "")
        self.remark_input = QTextEdit(product.remark if product else "")
        self.remark_input.setFixedHeight(80)
        self.colors_input = QLineEdit(product.colors if product else "")
        self.color_distribution_inputs: list[QLineEdit] = []
        self.color_value_inputs: list[QLineEdit] = []
        for index in range(1, 7):
            distribution = getattr(product, f"color_distribution_{index}", "") if product else ""
            color_value = getattr(product, f"color_{index}", "") if product else ""
            self.color_distribution_inputs.append(QLineEdit(distribution))
            self.color_value_inputs.append(QLineEdit(color_value))
        self.platform_price_input = QLineEdit(str(product.platform_price_ht or "") if product else "")
        self.platform_promo_input = QLineEdit(product.platform_promo if product else "")
        self.status_label = QLabel(_product_status_label(product) if product else "Brouillon")
        self.ms_last_seen_label = QLabel(_display_date(product.last_seen_at or "") if product else "")
        self.microstore_modified_label = QLabel(_display_date(product.last_microstore_modified_at or "") if product else "")
        self.local_modified_label = QLabel(_display_date(product.last_local_modified_at or "") if product else "")

        tabs = QTabWidget()
        general = QWidget()
        general_form = QFormLayout(general)
        general_form.addRow("Référence", self.ref_input)
        general_form.addRow("Nom", self.name_input)
        general_form.addRow("Catégorie", self.type_input)
        general_form.addRow("Prix HT", self.price_input)
        general_form.addRow("Colisage", self.package_input)
        general_form.addRow("Marque", self.brand_input)
        general_form.addRow("Année", self.year_input)
        general_form.addRow("Saison", self.season_input)
        general_form.addRow("Pièces hors colisage", self.pieces_outside_package_input)
        general_form.addRow("Poids (g)", self.weight_input)
        general_form.addRow("Pays d'origine", self.origin_input)
        general_form.addRow("Date de création", self.created_at_input)
        general_form.addRow("Stock connu", self.stock_input)
        general_form.addRow("Promo", self.promo_input)
        general_form.addRow("Remise (%)", self.discount_input)
        general_form.addRow("Remarque", self.remark_input)

        microstore = QWidget()
        microstore_form = QFormLayout(microstore)
        microstore_form.addRow("Statut actuel", self.status_label)
        microstore_form.addRow("Dernière vue Microstore", self.ms_last_seen_label)
        microstore_form.addRow("Dernière modification Microstore", self.microstore_modified_label)
        microstore_form.addRow("Dernière modification locale", self.local_modified_label)

        platforms = QWidget()
        platform_form = QFormLayout(platforms)
        platform_form.addRow("Contenu colis", self.content_input)
        platform_form.addRow("Composition matérielle", self.composition_input)
        platform_form.addRow("Couleur", self.color_input)
        platform_form.addRow("Couleurs", self.colors_input)
        for index in range(6):
            platform_form.addRow(f"Répartition {index + 1}", self.color_distribution_inputs[index])
            platform_form.addRow(f"Couleur {index + 1}", self.color_value_inputs[index])
        platform_form.addRow("Prix plateformes", self.platform_price_input)
        platform_form.addRow("Promo plateformes", self.platform_promo_input)

        tabs.addTab(general, "Général")
        tabs.addTab(microstore, "Microstore")
        tabs.addTab(platforms, "Plateformes")
        layout.addWidget(tabs, 1)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        actions = QHBoxLayout()
        save_button = make_button("Sauver")
        save_button.clicked.connect(self._save)
        cancel_button = make_button("Annuler")
        cancel_button.clicked.connect(self.reject)
        actions.addStretch(1)
        actions.addWidget(save_button)
        actions.addWidget(cancel_button)
        layout.addLayout(actions)

    def _save(self) -> None:
        try:
            ref = self.ref_input.text().strip().upper()
            if not ref:
                raise ValueError("Référence obligatoire.")
            price = _decimal_from_text(self.price_input.text())
            platform_price = _decimal_from_text(self.platform_price_input.text())
            discount = _decimal_from_text(self.discount_input.text())
            package_size = self.package_input.value() or None
            stock_value = self.stock_input.value()
            pieces_outside_package = self.pieces_outside_package_input.value() or None
            weight = self.weight_input.value() or None
            workflow_status = "modified" if self.product and self.product.workflow_status not in {"draft", "to_create"} else "draft"
            self.saved_product = Product(
                id=self.product.id if self.product else None,
                ref=ref,
                type_label=self.type_input.text().strip(),
                name=self.name_input.text().strip(),
                unit_price_ht=price,
                package_size=package_size,
                active=True,
                microstore_status=self.product.microstore_status if self.product else "",
                content_label=self.content_input.text().strip(),
                composition=self.composition_input.text().strip(),
                color=self.color_input.text().strip(),
                stock_snapshot=stock_value if stock_value >= 0 else None,
                brand=self.brand_input.text().strip(),
                year=self.year_input.text().strip(),
                season=self.season_input.text().strip(),
                pieces_outside_package=pieces_outside_package,
                weight_grams=weight,
                origin_country=self.origin_input.text().strip(),
                created_at=self.product.created_at if self.product else None,
                promo=self.promo_input.text().strip(),
                discount_percent=discount,
                remark=self.remark_input.toPlainText().strip(),
                colors=self.colors_input.text().strip(),
                color_distribution_1=self.color_distribution_inputs[0].text().strip(),
                color_1=self.color_value_inputs[0].text().strip(),
                color_distribution_2=self.color_distribution_inputs[1].text().strip(),
                color_2=self.color_value_inputs[1].text().strip(),
                color_distribution_3=self.color_distribution_inputs[2].text().strip(),
                color_3=self.color_value_inputs[2].text().strip(),
                color_distribution_4=self.color_distribution_inputs[3].text().strip(),
                color_4=self.color_value_inputs[3].text().strip(),
                color_distribution_5=self.color_distribution_inputs[4].text().strip(),
                color_5=self.color_value_inputs[4].text().strip(),
                color_distribution_6=self.color_distribution_inputs[5].text().strip(),
                color_6=self.color_value_inputs[5].text().strip(),
                platform_price_ht=platform_price,
                platform_promo=self.platform_promo_input.text().strip(),
                workflow_status=workflow_status,
                last_seen_at=self.product.last_seen_at if self.product else None,
                last_microstore_modified_at=self.product.last_microstore_modified_at if self.product else None,
                last_local_modified_at=self.product.last_local_modified_at if self.product else None,
                raw=self.product.raw if self.product else {},
                last_imported_at=self.product.last_imported_at if self.product else None,
            )
        except Exception as exc:
            self.message_label.setText(str(exc))
            return
        self.accept()


class SyncWorker(QObject):
    progress = Signal(str, int, str)
    source_finished = Signal(str, object)
    source_error = Signal(str, str)
    all_finished = Signal(object)

    def __init__(
        self,
        sources: list[str],
        microstore_token: str,
        microstore_days: int,
        portal_limit: int,
        efashion_email: str,
        efashion_password: str,
        pfs_email: str,
        pfs_password: str,
        portal_sessions: dict[str, dict] | None = None,
    ) -> None:
        super().__init__()
        self.sources = sources
        self.microstore_token = microstore_token
        self.microstore_days = microstore_days
        self.portal_limit = portal_limit
        self.efashion_email = efashion_email
        self.efashion_password = efashion_password
        self.pfs_email = pfs_email
        self.pfs_password = pfs_password
        self.portal_sessions = portal_sessions or {}
        self.cancel_requested = False

    def cancel(self) -> None:
        self.cancel_requested = True

    def run(self) -> None:
        result = {"sources": {}, "errors": {}, "cancelled": False, "sessions": {}}
        db = Database(default_db_path())
        resolver = Resolver(db)
        try:
            if "MicrostoreProducts" in self.sources and not self.cancel_requested:
                self._run_microstore_products(db, result)
            if "Microstore" in self.sources and not self.cancel_requested:
                self._run_microstore(db, resolver, result)
            if "eFashion" in self.sources and not self.cancel_requested:
                self._run_efashion(db, resolver, result)
            if "PFS" in self.sources and not self.cancel_requested:
                self._run_pfs(db, resolver, result)
        finally:
            db.close()
            self.all_finished.emit(result)

    def _run_microstore_products(self, db: Database, result: dict) -> None:
        source = "MicrostoreProducts"
        product_count = 0
        try:
            self._raise_if_cancelled()
            self._emit_progress(source, 1, "connexion API")
            connector = MicrostoreConnector(self.microstore_token)
            self._raise_if_cancelled()
            self._emit_progress(source, 20, "recuperation produits actifs/desactives")
            products = connector.list_products()
            product_count = db.upsert_products(products, mark_missing=True)
            summary_payload = {"orders": 0, "products": product_count, "cancelled": False}
            result["sources"][source] = summary_payload
            self._emit_progress(source, 100, f"{product_count} produits sauvegardes")
            self.source_finished.emit(source, summary_payload)
        except InterruptedError:
            summary_payload = {"orders": 0, "products": product_count, "cancelled": True}
            result["sources"][source] = summary_payload
            result["cancelled"] = True
            self._emit_progress(source, 100, f"annule - {product_count} produits sauvegardes")
            self.source_finished.emit(source, summary_payload)
        except Exception as exc:
            message = str(exc)
            result["errors"][source] = message
            self._emit_progress(source, 100, f"erreur - {message}")
            self.source_error.emit(source, message)

    def _run_microstore(self, db: Database, resolver: Resolver, result: dict) -> None:
        source = "Microstore"
        saved_orders = 0
        client_count = 0
        try:
            self._raise_if_cancelled()
            self._emit_progress(source, 1, "connexion API")
            connector = MicrostoreConnector(self.microstore_token)
            try:
                self._emit_progress(source, 12, "recuperation clients")
                clients = connector.list_clients()
                client_count = db.upsert_clients(clients)
                self._emit_progress(source, 24, f"{client_count} clients sauvegardes")
            except Exception as exc:
                db.log("microstore_clients_error", str(exc))
                self._emit_progress(source, 24, f"clients indisponibles - {exc}")
            summaries = connector.list_orders(days=self.microstore_days)
            total = len(summaries)
            self._emit_progress(source, 30, f"{total} commandes trouvees")
            for index, summary in enumerate(summaries, start=1):
                self._raise_if_cancelled()
                detail = connector.get_order(summary.order_id)
                self._persist_order(db, resolver, summary, detail)
                saved_orders += 1
                self._emit_detail_progress(source, index, total, saved_orders)
            summary_payload = {"orders": saved_orders, "products": 0, "clients": client_count, "cancelled": False}
            result["sources"][source] = summary_payload
            self._emit_progress(source, 100, f"{saved_orders} commandes, {client_count} clients")
            self.source_finished.emit(source, summary_payload)
        except InterruptedError:
            summary_payload = {"orders": saved_orders, "products": 0, "clients": client_count, "cancelled": True}
            result["sources"][source] = summary_payload
            result["cancelled"] = True
            self._emit_progress(source, 100, f"annule - {saved_orders} commandes sauvegardees")
            self.source_finished.emit(source, summary_payload)
        except Exception as exc:
            message = str(exc)
            result["errors"][source] = message
            self._emit_progress(source, 100, f"erreur - {message}")
            self.source_error.emit(source, message)

    def _run_efashion(self, db: Database, resolver: Resolver, result: dict) -> None:
        source = "eFashion"
        saved_orders = 0
        try:
            self._raise_if_cancelled()
            self._emit_progress(source, 1, "session")
            connector = EfashionConnector()
            restored = _session_from_settings(source, self.portal_sessions.get(source))
            if restored:
                connector.restore_session(restored)
                self._emit_progress(source, 8, f"session sauvegardee ({restored.user_label or 'compte'})")
            try:
                if not restored:
                    raise PortalApiError("session absente")
                connector.list_orders(page=1, limit=1)
                session = restored
            except Exception:
                self._emit_progress(source, 10, "connexion par identifiants")
                session = connector.login(self.efashion_email, self.efashion_password)
            result["sessions"][source] = _session_to_settings(session)
            self._raise_if_cancelled()
            self._emit_progress(source, 20, "recuperation commandes")
            summaries = connector.list_orders(page=1, limit=self.portal_limit)
            total = len(summaries)
            self._emit_progress(source, 30, f"{total} commandes trouvees")
            for index, summary in enumerate(summaries, start=1):
                self._raise_if_cancelled()
                detail = connector.get_order(summary.order_id)
                self._persist_order(db, resolver, summary, detail)
                saved_orders += 1
                self._emit_detail_progress(source, index, total, saved_orders)
            summary_payload = {"orders": saved_orders, "products": 0, "clients": 0, "user": session.user_label, "cancelled": False}
            result["sources"][source] = summary_payload
            self._emit_progress(source, 100, f"{saved_orders} commandes sauvegardees")
            self.source_finished.emit(source, summary_payload)
        except InterruptedError:
            summary_payload = {"orders": saved_orders, "products": 0, "clients": 0, "cancelled": True}
            result["sources"][source] = summary_payload
            result["cancelled"] = True
            self._emit_progress(source, 100, f"annule - {saved_orders} commandes sauvegardees")
            self.source_finished.emit(source, summary_payload)
        except Exception as exc:
            message = str(exc)
            result["errors"][source] = message
            self._emit_progress(source, 100, f"erreur - {message}")
            self.source_error.emit(source, message)

    def _run_pfs(self, db: Database, resolver: Resolver, result: dict) -> None:
        source = "PFS"
        saved_orders = 0
        try:
            self._raise_if_cancelled()
            self._emit_progress(source, 1, "session")
            connector = PfsConnector()
            restored = _session_from_settings(source, self.portal_sessions.get(source))
            if restored:
                connector.restore_session(restored)
                self._emit_progress(source, 8, f"session sauvegardee ({restored.user_label or 'compte'})")
            try:
                if not restored:
                    raise PortalApiError("session absente")
                connector.list_orders(page=1, per_page=1)
                session = restored
            except Exception:
                self._emit_progress(source, 10, "connexion par identifiants")
                session = connector.login(self.pfs_email, self.pfs_password)
            result["sessions"][source] = _session_to_settings(session)
            self._raise_if_cancelled()
            self._emit_progress(source, 20, "recuperation commandes")
            summaries = connector.list_orders(page=1, per_page=self.portal_limit)
            total = len(summaries)
            self._emit_progress(source, 30, f"{total} commandes trouvees")
            for index, summary in enumerate(summaries, start=1):
                self._raise_if_cancelled()
                detail = connector.get_order(summary.order_id)
                self._persist_order(db, resolver, summary, detail)
                saved_orders += 1
                self._emit_detail_progress(source, index, total, saved_orders)
            summary_payload = {"orders": saved_orders, "products": 0, "clients": 0, "user": session.user_label, "cancelled": False}
            result["sources"][source] = summary_payload
            self._emit_progress(source, 100, f"{saved_orders} commandes sauvegardees")
            self.source_finished.emit(source, summary_payload)
        except InterruptedError:
            summary_payload = {"orders": saved_orders, "products": 0, "clients": 0, "cancelled": True}
            result["sources"][source] = summary_payload
            result["cancelled"] = True
            self._emit_progress(source, 100, f"annule - {saved_orders} commandes sauvegardees")
            self.source_finished.emit(source, summary_payload)
        except Exception as exc:
            message = str(exc)
            result["errors"][source] = message
            self._emit_progress(source, 100, f"erreur - {message}")
            self.source_error.emit(source, message)

    def _persist_order(self, db: Database, resolver: Resolver, summary: PortalOrderSummary, detail: PortalOrder) -> None:
        lines = resolver.lines_from_portal_lines(detail.lines, source=detail.source)
        db.upsert_cached_order(summary, detail, _status_from_lines(lines))

    def _emit_detail_progress(self, source: str, index: int, total: int, saved_orders: int) -> None:
        percent = 100 if total <= 0 else 30 + round((index / total) * 65)
        self._emit_progress(source, min(percent, 95), f"details {index}/{total} - {saved_orders} sauvegardees")

    def _emit_progress(self, source: str, percent: int, message: str) -> None:
        self.progress.emit(source, max(0, min(100, percent)), message)

    def _raise_if_cancelled(self) -> None:
        if self.cancel_requested:
            raise InterruptedError


class SyncCompletionRelay(QObject):
    finished = Signal(object, object, object, bool)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.resolver = Resolver(self.db)
        self.settings = load_settings()
        self.lines: list[InvoiceLine] = []
        self.current_order_path: Path | None = None
        self.current_order_source = ""
        self.current_order_key = ""
        self.detected_order_files: list[OrderFile] = []
        self.order_status_cache: dict[Path, str] = {}
        self.detected_product_export = None
        self.microstore_connector = MicrostoreConnector(self.settings.microstore_api_token)
        self.efashion_connector = EfashionConnector()
        self.pfs_connector = PfsConnector()
        self.portal_summaries: dict[tuple[str, str], PortalOrderSummary] = {}
        self.portal_details: dict[tuple[str, str], PortalOrder] = {}
        self.portal_status_cache: dict[tuple[str, str], str] = {}
        self.clients: list[PortalClient] = []
        self.product_rows: list[Product] = []
        self.product_model = ProductTableModel()
        self.product_proxy = ProductFilterProxyModel()
        self.product_proxy.setSourceModel(self.product_model)
        self.sync_threads: list[QThread] = []
        self.sync_workers: list[SyncWorker] = []
        self.sync_buttons: dict[str, QPushButton] = {}
        self.sync_progress_bars: dict[str, QProgressBar] = {}
        self.sync_status_labels: dict[str, QLabel] = {}
        self.sync_active_sources: set[str] = set()
        self.sync_thread_sources: dict[QThread, set[str]] = {}
        self.sync_thread_workers: dict[QThread, SyncWorker] = {}
        self.sync_all_active = False
        self.injection_control_dialog: InjectionControlDialog | None = None
        self.sync_completion_relay = SyncCompletionRelay(self)
        self.sync_completion_relay.finished.connect(self._on_sync_all_finished)

        self.setWindowTitle(APP_NAME)
        self.resize(1100, 700)
        self._build_ui()
        self._load_cached_orders()
        self._refresh_clients_page()
        self._apply_window_flags()
        self._refresh_status()
        self._refresh_missing_types()
        self._refresh_product_folder()
        self._refresh_products_page()
        self._refresh_order_folder()
        self._refresh_mappings_table()
        self._refresh_products_page()

        self.sage_watch_timer = QTimer(self)
        self.sage_watch_timer.timeout.connect(self._watch_sage_process)
        self.sage_watch_timer.start(3000)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        for worker in list(self.sync_workers):
            worker.cancel()
        for thread in list(self.sync_threads):
            if thread.isRunning():
                thread.quit()
                thread.wait(1500)
        save_settings(self.settings)
        self.db.close()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setMinimumWidth(0)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setFixedWidth(145)
        sidebar.setFrameShape(QFrame.StyledPanel)
        sidebar_layout = QVBoxLayout(sidebar)
        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignCenter)
        self.commands_nav = QPushButton("Commandes")
        self.commands_nav.setCheckable(True)
        self.commands_nav.setChecked(True)
        self.products_nav = QPushButton("Produits")
        self.products_nav.setCheckable(True)
        self.clients_nav = QPushButton("Clients")
        self.clients_nav.setCheckable(True)
        self.settings_nav = QPushButton("Réglages")
        self.settings_nav.setCheckable(True)
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(self.commands_nav)
        sidebar_layout.addWidget(self.products_nav)
        sidebar_layout.addWidget(self.clients_nav)
        sidebar_layout.addWidget(self.settings_nav)
        sidebar_layout.addStretch(1)

        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(0)
        self.stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.commands_page = self._build_commands_page()
        self.products_page = self._build_products_page()
        self.clients_page = self._build_clients_page()
        self.settings_page = self._build_settings_page()
        self.stack.addWidget(self.commands_page)
        self.stack.addWidget(self.products_page)
        self.stack.addWidget(self.clients_page)
        self.stack.addWidget(self.settings_page)
        self.commands_nav.clicked.connect(lambda: self._show_page(0))
        self.products_nav.clicked.connect(lambda: self._show_page(1))
        self.clients_nav.clicked.connect(lambda: self._show_page(2))
        self.settings_nav.clicked.connect(lambda: self._show_page(3))

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.commands_nav.setChecked(index == 0)
        self.products_nav.setChecked(index == 1)
        self.clients_nav.setChecked(index == 2)
        self.settings_nav.setChecked(index == 3)

    def _build_commands_page(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        layout = QVBoxLayout(page)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        filters = QHBoxLayout()
        self.order_search = QLineEdit()
        self.order_search.setMinimumWidth(0)
        self.order_search.setPlaceholderText("Rechercher client, commande, email, telephone...")
        self.order_search.textChanged.connect(self._apply_order_filters)
        self.source_filter = QComboBox()
        self.source_filter.addItems(ORDER_SOURCES)
        self.source_filter.currentTextChanged.connect(self._apply_order_filters)
        self.status_filter = QComboBox()
        self.status_filter.addItems(ORDER_STATUSES)
        self.status_filter.currentTextChanged.connect(self._apply_order_filters)
        self.date_filter = QComboBox()
        self.date_filter.addItems(DATE_FILTERS)
        self.date_filter.currentTextChanged.connect(self._apply_order_filters)
        filters.addWidget(self.order_search, 1)
        filters.addWidget(QLabel("Source"))
        filters.addWidget(self.source_filter)
        filters.addWidget(QLabel("Statut"))
        filters.addWidget(self.status_filter)
        filters.addWidget(QLabel("Date"))
        filters.addWidget(self.date_filter)
        layout.addLayout(filters)

        self.order_table = QTableWidget(0, len(COMMAND_HEADERS))
        self.order_table.setHorizontalHeaderLabels(COMMAND_HEADERS)
        configure_table_columns(
            self.order_table,
            {0: 90, 1: 130, 3: 135, 4: 55, 5: 90, 6: 95},
            {2},
        )
        self.order_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.order_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.order_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.order_table.cellDoubleClicked.connect(lambda _row, _col: self._open_selected_order_detail())
        layout.addWidget(self.order_table, 1)

        actions = QGridLayout()
        import_order_button = make_button("Importer")
        import_order_button.setToolTip("Importer un fichier commande")
        import_order_button.clicked.connect(self._import_order)
        sync_orders = make_button("Synchroniser")
        sync_orders.clicked.connect(lambda: self._start_sync(["Microstore", "eFashion", "PFS"]))
        detail_button = make_button("Détails")
        detail_button.setToolTip("Ouvrir les détails de la commande")
        detail_button.clicked.connect(self._open_selected_order_detail)
        open_web_button = make_button("Site")
        open_web_button.setToolTip("Ouvrir la commande sur le site")
        open_web_button.clicked.connect(self._open_selected_order_web_page)
        copy_link_button = make_button("Copier lien")
        copy_link_button.clicked.connect(self._copy_selected_order_web_link)
        open_client_button = make_button("Ouvrir client")
        open_client_button.clicked.connect(self._open_selected_order_client)
        inject_selected = make_button("Injecter")
        inject_selected.setToolTip("Injecter dans Sage")
        inject_selected.clicked.connect(self._inject_selected_order_from_folder)
        quick_invoice = make_button("Facture rapide")
        quick_invoice.clicked.connect(self._open_quick_invoice_dialog)
        delete_order = make_button("Supprimer")
        delete_order.clicked.connect(self._delete_selected_order)
        clear_orders = make_button("Vider commandes")
        clear_orders.clicked.connect(self._clear_all_orders)
        command_buttons = [
            import_order_button,
            quick_invoice,
            sync_orders,
            inject_selected,
            detail_button,
            open_web_button,
            copy_link_button,
            open_client_button,
            delete_order,
            clear_orders,
        ]
        for index, button in enumerate(command_buttons):
            actions.addWidget(button, index // 5, index % 5)
        for column in range(5):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        return page

    def _build_products_page(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        layout = QVBoxLayout(page)

        self.product_status_label = QLabel("")
        layout.addWidget(self.product_status_label)

        filters = QHBoxLayout()
        self.product_search = QLineEdit()
        self.product_search.setMinimumWidth(0)
        self.product_search.setPlaceholderText("Rechercher référence, catégorie, nom...")
        self.product_search.textChanged.connect(self._apply_product_filters)
        self.product_type_filter = QComboBox()
        self.product_type_filter.currentTextChanged.connect(self._apply_product_filters)
        self.product_status_filter = QComboBox()
        self.product_status_filter.addItems(PRODUCT_STATUSES)
        self.product_status_filter.currentTextChanged.connect(self._apply_product_filters)
        filters.addWidget(self.product_search, 1)
        filters.addWidget(QLabel("Catégorie"))
        filters.addWidget(self.product_type_filter)
        filters.addWidget(QLabel("Statut"))
        filters.addWidget(self.product_status_filter)
        layout.addLayout(filters)

        self.product_table = QTableView()
        self.product_table.setMinimumWidth(0)
        self.product_table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.product_table.setModel(self.product_proxy)
        self.product_table.setSortingEnabled(True)
        self.product_table.sortByColumn(6, Qt.DescendingOrder)
        self.product_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.product_table.horizontalHeader().setStretchLastSection(False)
        self.product_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.product_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.product_table.verticalHeader().setDefaultSectionSize(28)
        self.product_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.product_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.product_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.product_table.doubleClicked.connect(lambda _index: self._edit_selected_product())
        layout.addWidget(self.product_table, 1)

        actions = QGridLayout()
        refresh_button = make_button("Recharger")
        refresh_button.setToolTip("Recharger l'affichage depuis le cache local")
        refresh_button.clicked.connect(self._refresh_products_page)
        sync_button = make_button("Sync Microstore")
        sync_button.clicked.connect(lambda: self._start_sync(["MicrostoreProducts"]))
        new_button = make_button("Nouveau")
        new_button.clicked.connect(self._new_product_draft)
        edit_button = make_button("Modifier")
        edit_button.clicked.connect(self._edit_selected_product)
        simulate_button = make_button("Simulation")
        simulate_button.clicked.connect(self._simulate_selected_product)
        apply_button = make_button("Appliquer MS")
        apply_button.setToolTip("Appliquer à Microstore")
        apply_button.clicked.connect(self._apply_selected_product_to_microstore)
        disable_button = make_button("Désactiver")
        disable_button.clicked.connect(lambda: self._set_selected_product_active(False))
        reactivate_button = make_button("Réactiver")
        reactivate_button.clicked.connect(lambda: self._set_selected_product_active(True))
        for index, button in enumerate((refresh_button, sync_button, new_button, edit_button, simulate_button, apply_button, disable_button, reactivate_button)):
            actions.addWidget(button, index // 4, index % 4)
        for column in range(4):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        return page

    def _build_clients_page(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(0)
        layout = QVBoxLayout(page)

        self.clients_status_label = QLabel("")
        layout.addWidget(self.clients_status_label)

        filters = QHBoxLayout()
        self.client_search = QLineEdit()
        self.client_search.setMinimumWidth(0)
        self.client_search.setPlaceholderText("Rechercher client, societe, telephone, email, ville...")
        self.client_search.textChanged.connect(self._apply_client_filters)
        self.client_source_filter = QComboBox()
        self.client_source_filter.addItems(["Microstore"])
        self.client_source_filter.currentTextChanged.connect(self._apply_client_filters)
        filters.addWidget(self.client_search, 1)
        filters.addWidget(QLabel("Source"))
        filters.addWidget(self.client_source_filter)
        layout.addLayout(filters)

        self.clients_table = QTableWidget(0, len(CLIENT_HEADERS))
        self.clients_table.setHorizontalHeaderLabels(CLIENT_HEADERS)
        configure_table_columns(self.clients_table, {0: 90, 1: 170, 3: 120, 4: 210, 5: 120, 6: 100}, {2})
        self.clients_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.clients_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.clients_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.clients_table.cellDoubleClicked.connect(lambda _row, _col: self._open_selected_client_detail())
        layout.addWidget(self.clients_table, 1)

        actions = QGridLayout()
        refresh_button = make_button("Recharger")
        refresh_button.clicked.connect(self._refresh_clients_page)
        sync_button = make_button("Sync Microstore")
        sync_button.clicked.connect(lambda: self._start_sync(["Microstore"]))
        detail_button = make_button("Fiche client")
        detail_button.clicked.connect(self._open_selected_client_detail)
        for index, button in enumerate((refresh_button, sync_button, detail_button)):
            actions.addWidget(button, 0, index)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)
        return page

    def _build_settings_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setMinimumWidth(0)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(page)

        layout.addWidget(self._build_portals_section())
        layout.addWidget(self._build_sage_section())
        layout.addWidget(self._build_mappings_section())
        layout.addWidget(self._build_injection_section())

        save_row = QHBoxLayout()
        self.missing_types = QLabel("")
        self.missing_types.setWordWrap(True)
        self.missing_types.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        save_settings_button = make_button("Sauver reglages")
        save_settings_button.clicked.connect(self._save_app_settings)
        save_row.addWidget(self.missing_types, 1)
        save_row.addWidget(save_settings_button)
        layout.addLayout(save_row)
        scroll.setWidget(page)
        return scroll

    def _build_portals_section(self) -> QGroupBox:
        box = QGroupBox("Synchronisation")
        layout = QVBoxLayout(box)

        microstore_grid = QGridLayout()
        self.microstore_token = QLineEdit(self.settings.microstore_api_token)
        self.microstore_token.setPlaceholderText("Token admin_token Microstore")
        self.microstore_token.setEchoMode(QLineEdit.Password)
        self.microstore_token.setMinimumWidth(0)
        self.microstore_token.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.microstore_days = QSpinBox()
        self.microstore_days.setRange(1, 365)
        self.microstore_days.setSuffix(" jours")
        self.microstore_days.setValue(self.settings.microstore_sync_days)
        self.microstore_product_resync_hours = QSpinBox()
        self.microstore_product_resync_hours.setRange(0, 720)
        self.microstore_product_resync_hours.setValue(self.settings.microstore_product_resync_hours)
        self.microstore_product_resync_hours.setSuffix(" h")
        self.microstore_product_resync_hours.setSpecialValueText("toujours")
        self.portal_order_limit = QSpinBox()
        self.portal_order_limit.setRange(1, 1000)
        self.portal_order_limit.setValue(self.settings.portal_order_limit)
        self.portal_order_limit.setSuffix(" commandes")
        microstore_grid.addWidget(QLabel("Token"), 0, 0)
        microstore_grid.addWidget(self.microstore_token, 0, 1, 1, 3)
        microstore_grid.addWidget(QLabel("Historique"), 1, 0)
        microstore_grid.addWidget(self.microstore_days, 1, 1)
        microstore_grid.addWidget(QLabel("Resync produits"), 1, 2)
        microstore_grid.addWidget(self.microstore_product_resync_hours, 1, 3)
        microstore_grid.addWidget(QLabel("Limite PFS/eFashion"), 2, 0)
        microstore_grid.addWidget(self.portal_order_limit, 2, 1)
        microstore_grid.setColumnStretch(1, 1)
        layout.addLayout(microstore_grid)

        efashion_row = QGridLayout()
        self.efashion_email = QLineEdit(self.settings.efashion_email or self.settings.portal_email)
        self.efashion_password = QLineEdit(self.settings.efashion_password)
        self.efashion_password.setEchoMode(QLineEdit.Password)
        self.efashion_email.setMinimumWidth(0)
        self.efashion_password.setMinimumWidth(0)
        efashion_row.addWidget(QLabel("Email eFashion"), 0, 0)
        efashion_row.addWidget(self.efashion_email, 0, 1)
        efashion_row.addWidget(QLabel("MDP eFashion"), 1, 0)
        efashion_row.addWidget(self.efashion_password, 1, 1)
        efashion_row.setColumnStretch(1, 1)
        layout.addLayout(efashion_row)

        pfs_row = QGridLayout()
        self.pfs_email = QLineEdit(self.settings.pfs_email or self.settings.portal_email)
        self.pfs_password = QLineEdit(self.settings.pfs_password)
        self.pfs_password.setEchoMode(QLineEdit.Password)
        self.pfs_email.setMinimumWidth(0)
        self.pfs_password.setMinimumWidth(0)
        pfs_row.addWidget(QLabel("Email PFS"), 0, 0)
        pfs_row.addWidget(self.pfs_email, 0, 1)
        pfs_row.addWidget(QLabel("MDP PFS"), 1, 0)
        pfs_row.addWidget(self.pfs_password, 1, 1)
        pfs_row.setColumnStretch(1, 1)
        layout.addLayout(pfs_row)

        actions = QGridLayout()
        self.microstore_status = QLabel("Microstore: non configure")
        self.efashion_status = QLabel("eFashion: non connecte")
        self.pfs_status = QLabel("PFS: non connecte")
        for status_label in (self.microstore_status, self.efashion_status, self.pfs_status):
            status_label.setMinimumWidth(0)
            status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            status_label.setWordWrap(False)
        sync_microstore = make_button("Sync Microstore")
        sync_products = make_button("Sync DB produits")
        sync_efashion = make_button("Sync eFashion")
        sync_pfs = make_button("Sync PFS")
        sync_button = make_button("Sync tout")
        self.cancel_sync_button = make_button("Annuler")
        self.cancel_sync_button.setEnabled(False)
        sync_microstore.clicked.connect(lambda: self._start_sync(["Microstore"]))
        sync_products.clicked.connect(lambda: self._start_sync(["MicrostoreProducts"]))
        sync_efashion.clicked.connect(lambda: self._start_sync(["eFashion"]))
        sync_pfs.clicked.connect(lambda: self._start_sync(["PFS"]))
        sync_button.clicked.connect(lambda: self._start_sync(["Microstore", "eFashion", "PFS"]))
        self.cancel_sync_button.clicked.connect(self._cancel_sync)
        self.sync_buttons = {
            "Microstore": sync_microstore,
            "MicrostoreProducts": sync_products,
            "eFashion": sync_efashion,
            "PFS": sync_pfs,
            "all": sync_button,
        }
        for index, button in enumerate((sync_products, sync_microstore, sync_efashion, sync_pfs, sync_button, self.cancel_sync_button)):
            actions.addWidget(button, index // 3, index % 3)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        layout.addLayout(actions)

        progress_layout = QGridLayout()
        progress_layout.setColumnStretch(2, 1)
        self.sync_progress_bars = {}
        self.sync_status_labels = {}
        for row_index, source in enumerate(("Microstore", "eFashion", "PFS")):
            source_label = QLabel(source)
            source_label.setFixedWidth(72)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("%p%")
            bar.setFixedWidth(140)
            bar.setFixedHeight(18)
            bar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            status_label = {
                "Microstore": self.microstore_status,
                "eFashion": self.efashion_status,
                "PFS": self.pfs_status,
            }[source]
            status_label.setText(f"{source}: pret")
            self.sync_progress_bars[source] = bar
            self.sync_status_labels[source] = status_label
            progress_layout.addWidget(source_label, row_index, 0)
            progress_layout.addWidget(bar, row_index, 1)
            progress_layout.addWidget(status_label, row_index, 2)
        layout.addLayout(progress_layout)

        self.sync_summary = QLabel("")
        self.sync_summary.setWordWrap(True)
        self.sync_summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.sync_summary)
        return box

    def _build_folders_section(self) -> QGroupBox:
        box = QGroupBox("Dossiers")
        layout = QVBoxLayout(box)

        product_folder_row = QHBoxLayout()
        self.product_folder_input = QLineEdit(self.settings.product_folder_path)
        self.product_folder_input.setPlaceholderText("Dossier MS_IMPORT Google Drive...")
        browse_product_folder = make_button("Parcourir")
        browse_product_folder.clicked.connect(self._choose_product_folder)
        product_folder_row.addWidget(QLabel("Import produits Microstore"))
        product_folder_row.addWidget(self.product_folder_input, 1)
        product_folder_row.addWidget(browse_product_folder)
        layout.addLayout(product_folder_row)

        product_actions = QHBoxLayout()
        self.product_export_status = QLabel("Aucune BDD Microstore detectee.")
        refresh_products = make_button("Detecter BDD")
        refresh_products.clicked.connect(self._refresh_product_folder)
        import_detected_products = make_button("Mettre a jour BDD")
        import_detected_products.clicked.connect(self._import_detected_product_export)
        import_products_button = make_button("Importer BDD articles...")
        import_products_button.clicked.connect(self._import_products)
        product_actions.addWidget(self.product_export_status, 1)
        product_actions.addWidget(refresh_products)
        product_actions.addWidget(import_detected_products)
        product_actions.addWidget(import_products_button)
        layout.addLayout(product_actions)

        folder_row = QHBoxLayout()
        self.order_folder_input = QLineEdit(self.settings.order_folder_path)
        self.order_folder_input.setPlaceholderText("Dossier commandes Microstore...")
        browse_order_folder = make_button("Parcourir")
        browse_order_folder.clicked.connect(self._choose_order_folder)
        folder_row.addWidget(QLabel("Commandes Microstore"))
        folder_row.addWidget(self.order_folder_input, 1)
        folder_row.addWidget(browse_order_folder)
        layout.addLayout(folder_row)

        return box

    def _build_sage_section(self) -> QGroupBox:
        box = QGroupBox("Sage")
        form = QFormLayout(box)
        self.ahk_path = QLineEdit(self.settings.autohotkey_path)
        self.sage_path = QLineEdit(self.settings.sage_executable_path)
        self.window_title = QLineEdit(self.settings.sage_profile.window_title_contains)
        for line_edit in (self.ahk_path, self.sage_path, self.window_title):
            line_edit.setMinimumWidth(0)
            line_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.auto_close = QCheckBox("Fermer l'assistant quand Sage se ferme")
        self.auto_close.setChecked(self.settings.auto_close_with_sage)
        self.always_on_top = QCheckBox("Epingler la fenetre")
        self.always_on_top.setChecked(self.settings.always_on_top)
        self.always_on_top.toggled.connect(self._toggle_always_on_top)
        form.addRow("Sage.exe", self.sage_path)
        form.addRow("Titre fenetre Sage contient", self.window_title)
        form.addRow("AutoHotkey.exe", self.ahk_path)
        form.addRow("", self.auto_close)
        form.addRow("", self.always_on_top)
        return box

    def _build_mappings_section(self) -> QGroupBox:
        box = QGroupBox("Mappings Sage")
        layout = QGridLayout(box)
        mappings_button = make_button("Mappings Sage")
        mappings_button.clicked.connect(self._open_mappings_dialog)
        label = QLabel("Configurer les associations categorie fournisseur -> code Sage.")
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(label, 0, 0)
        layout.addWidget(mappings_button, 0, 1)
        layout.setColumnStretch(0, 1)
        return box

    def _build_injection_section(self) -> QGroupBox:
        box = QGroupBox("Injection")
        layout = QGridLayout(box)
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(0, 2000)
        self.delay_ms.setSpecialValueText("0 instant")
        self.delay_ms.setSuffix(" ms")
        self.delay_ms.setValue(self.settings.sage_profile.delay_ms)
        self.delay_ms.setToolTip("Délai unique utilisé entre les actions clavier Sage.")
        self.confirmation_mode = QComboBox()
        self.confirmation_mode.addItem("Direct", "direct")
        self.confirmation_mode.addItem("Simple", "simple")
        self.confirmation_mode.addItem("Debug", "debug")
        self.confirmation_mode.setToolTip("Direct: popup final seulement. Simple: controle avant + popup final. Debug: controles detailles. Les cases Captures/Logs restent independantes.")
        self.confirmation_mode.setCurrentIndex(max(0, self.confirmation_mode.findData(self.settings.sage_profile.confirmation_mode or "simple")))
        self.auto_capture = QCheckBox("Captures automatiques")
        self.auto_capture.setChecked(self.settings.sage_profile.capture_before_after)
        self.injection_logs = QCheckBox("Logs")
        self.injection_logs.setChecked(self.settings.sage_profile.log_enabled)
        diagnostic_button = make_button("Diagnostic Sage")
        diagnostic_button.clicked.connect(self._run_sage_diagnostics)
        layout.addWidget(QLabel("Confirmation"), 0, 0)
        layout.addWidget(self.confirmation_mode, 0, 1)
        layout.addWidget(QLabel("Délai injection"), 1, 0)
        layout.addWidget(self.delay_ms, 1, 1)
        layout.addWidget(self.auto_capture, 2, 0, 1, 2)
        layout.addWidget(self.injection_logs, 2, 2)
        layout.addWidget(diagnostic_button, 2, 3)
        return box

    def _build_database_section(self) -> QGroupBox:
        box = QGroupBox("Base de donnees")
        layout = QHBoxLayout(box)
        db_path = QLineEdit(str(default_db_path()))
        db_path.setReadOnly(True)
        db_path.setMinimumWidth(0)
        db_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        backup_button = make_button("Sauvegarder la base")
        backup_button.setEnabled(False)
        maintenance_button = make_button("Maintenance")
        maintenance_button.setEnabled(False)
        layout.addWidget(QLabel("SQLite"))
        layout.addWidget(db_path, 1)
        layout.addWidget(backup_button)
        layout.addWidget(maintenance_button)
        return box

    def _toggle_always_on_top(self, checked: bool) -> None:
        self.settings.always_on_top = checked
        self._apply_window_flags()
        self.show()

    def _apply_window_flags(self) -> None:
        flags = self.windowFlags()
        if self.settings.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _refresh_status(self) -> None:
        product_latest = self.db.latest_product_import()
        commands_latest = self.db.latest_cached_order_sync()
        parts = [
            f"Microstore : {self.db.count_cached_orders('Microstore')} commandes, {self.db.count_products()} produits",
            f"Factures rapides : {self.db.count_cached_orders(QUICK_INVOICE_SOURCE)}",
            f"eFashion : {self.db.count_cached_orders('eFashion')} commandes",
            f"PFS : {self.db.count_cached_orders('PFS')} commandes",
            f"Clients Microstore : {self.db.count_clients('Microstore')}",
            f"Produits Microstore : {_display_date(product_latest) if product_latest else 'aucune synchro'}",
            f"Cache commandes : {_display_date(commands_latest) if commands_latest else 'vide'}",
        ]
        self.status_label.setText(" | ".join(parts))

    def _refresh_missing_types(self) -> None:
        if not hasattr(self, "missing_types"):
            return
        missing = self.db.list_types_without_mapping()
        if missing:
            self.missing_types.setText("Types sans mapping: " + ", ".join(missing[:20]))
        else:
            self.missing_types.setText("Tous les types produits connus ont un mapping.")

    def _refresh_product_type_filter(self) -> None:
        if not hasattr(self, "product_type_filter"):
            return
        current = self.product_type_filter.currentText() or "Tous"
        types = ["Tous", *self.db.list_product_types()]
        self.product_type_filter.blockSignals(True)
        self.product_type_filter.clear()
        self.product_type_filter.addItems(types)
        self.product_type_filter.setCurrentText(current if current in types else "Tous")
        self.product_type_filter.blockSignals(False)

    def _refresh_products_page(self) -> None:
        if not hasattr(self, "product_table"):
            return
        self._refresh_product_type_filter()
        self.product_rows = self.db.list_products(limit=10_000)
        self.product_model.set_products(self.product_rows)
        self._apply_product_filters()
        self.product_table.sortByColumn(6, Qt.DescendingOrder)
        if self.product_table.columnWidth(0) < 90:
            widths = [110, 190, 180, 150, 90, 80, 150]
            for col, width in enumerate(widths):
                self.product_table.setColumnWidth(col, width)
        active = self.db.count_products_by_microstore_status("active")
        disabled = self.db.count_products_by_microstore_status("disabled")
        historical = self.db.count_products_by_workflow_status("historical")
        self.product_status_label.setText(
            f"{self.product_proxy.rowCount()} affichés | {active} actifs Microstore | {disabled} désactivés | {historical} historiques locaux | dernière synchro API {_display_date(self.db.latest_product_import() or '') or 'jamais'}"
        )

    def _apply_product_filters(self) -> None:
        if not hasattr(self, "product_proxy"):
            return
        search = self.product_search.text().strip() if hasattr(self, "product_search") else ""
        type_filter = self.product_type_filter.currentText() if hasattr(self, "product_type_filter") else "Tous"
        status_filter = self.product_status_filter.currentText() if hasattr(self, "product_status_filter") else "Tous"
        self.product_proxy.set_filters(search, type_filter, status_filter)
        if hasattr(self, "product_status_label"):
            active = self.db.count_products_by_microstore_status("active")
            disabled = self.db.count_products_by_microstore_status("disabled")
            historical = self.db.count_products_by_workflow_status("historical")
            self.product_status_label.setText(
                f"{self.product_proxy.rowCount()} affichés | {active} actifs Microstore | {disabled} désactivés | {historical} historiques locaux | dernière synchro API {_display_date(self.db.latest_product_import() or '') or 'jamais'}"
            )

    def _refresh_clients_page(self) -> None:
        if not hasattr(self, "clients_table"):
            return
        self.clients = self.db.list_clients(source="Microstore", limit=10_000)
        self._apply_client_filters()
        latest = self.db.latest_client_sync("Microstore")
        self.clients_status_label.setText(
            f"{self.db.count_clients('Microstore')} clients Microstore | dernière synchro {_display_date(latest or '') or 'jamais'}"
        )

    def _apply_client_filters(self) -> None:
        if not hasattr(self, "clients_table"):
            return
        source = self.client_source_filter.currentText() if hasattr(self, "client_source_filter") else "Microstore"
        search = self.client_search.text().strip() if hasattr(self, "client_search") else ""
        clients = self.db.list_clients(source=source, search=search, limit=10_000)
        self.clients = clients
        self.clients_table.setUpdatesEnabled(False)
        self.clients_table.blockSignals(True)
        try:
            self.clients_table.setRowCount(len(clients))
            for row, client in enumerate(clients):
                values = [
                    client.source,
                    client.name,
                    client.company,
                    client.phone,
                    client.email,
                    client.city,
                    client.country,
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(ROLE_KEY, client.client_key)
                    self.clients_table.setItem(row, col, item)
        finally:
            self.clients_table.blockSignals(False)
            self.clients_table.setUpdatesEnabled(True)
        if hasattr(self, "clients_status_label"):
            latest = self.db.latest_client_sync(source)
            self.clients_status_label.setText(
                f"{len(clients)} affichés | {self.db.count_clients(source)} clients {source} | dernière synchro {_display_date(latest or '') or 'jamais'}"
            )

    def _selected_client(self) -> PortalClient | None:
        if not hasattr(self, "clients_table"):
            return None
        rows = self.clients_table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        key_item = self.clients_table.item(row, 0)
        if not key_item:
            return None
        source = self.clients_table.item(row, 0).text()
        client_key = str(key_item.data(ROLE_KEY) or "")
        return self.db.get_client(source, client_key) if client_key else None

    def _open_selected_client_detail(self) -> None:
        client = self._selected_client()
        if client is None:
            QMessageBox.information(self, APP_NAME, "Aucun client sélectionné.")
            return
        self._open_client_detail(client)

    def _open_client_detail(self, client: PortalClient) -> None:
        lines = [
            ("Source", client.source),
            ("Société", client.company),
            ("Nom", client.name),
            ("Téléphone", client.phone),
            ("Email", client.email),
            ("Adresse", client.address),
            ("Code postal", client.zip_code),
            ("Ville", client.city),
            ("Pays", client.country),
            ("TVA", client.vat_number),
        ]
        text = "\n".join(f"{label}: {value}" for label, value in lines if value)
        if not text:
            text = "Aucune information détaillée disponible."
        QMessageBox.information(self, f"Client {client.company or client.name or client.client_key}", text)

    def _selected_product(self) -> Product | None:
        if not hasattr(self, "product_table"):
            return None
        indexes = self.product_table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_index = self.product_proxy.mapToSource(indexes[0])
        ref = self.product_model.data(self.product_model.index(source_index.row(), 0), Qt.UserRole)
        return self.db.get_product_by_ref(str(ref or ""))

    def _open_quick_invoice_dialog(self) -> None:
        dialog = QuickInvoiceDialog(self.db, self.resolver, self)
        dialog.exec()
        if not dialog.lines:
            return
        key = self._save_quick_invoice(dialog.lines)
        self.lines = dialog.lines
        self.current_order_source = QUICK_INVOICE_SOURCE
        self.current_order_key = key
        self.current_order_path = None
        self._load_cached_orders()
        self._refresh_status()
        if dialog.inject_requested:
            if self._prepare_injection():
                self._load_cached_orders()

    def _save_quick_invoice(self, lines: list[InvoiceLine]) -> str:
        for line in lines:
            line.validate()
        summary, detail = quick_invoice_to_portal_order(lines)
        key = summary.order_number or summary.order_id
        status = _status_from_lines(lines)
        self.db.upsert_cached_order(summary, detail, status)
        self.db.log("quick_invoice_save", f"{key}: {len(lines)} ligne(s) sauvegardee(s)")
        return key

    def _parse_quick_ref_text(self, text: str) -> tuple[str, int]:
        return parse_quick_ref_text(text)

    def _refresh_quick_suggestions(self) -> None:
        if not hasattr(self, "quick_suggestions"):
            return
        ref, _packages = self._parse_quick_ref_text(self.quick_ref_input.text())
        self.quick_suggestions.clear()
        if len(ref) < 2:
            return
        for product in self.db.search_products(ref, limit=8):
            label = f"{product.ref} | {product.name or product.type_label} | {product.unit_price_ht or ''} EUR | colisage {product.package_size or '?'}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, product.ref)
            self.quick_suggestions.addItem(item)

    def _quick_selected_ref(self) -> str:
        selected = self.quick_suggestions.selectedItems() if hasattr(self, "quick_suggestions") else []
        if selected:
            return str(selected[0].data(Qt.UserRole) or "")
        ref, _packages = self._parse_quick_ref_text(self.quick_ref_input.text())
        return ref

    def _add_quick_invoice_line(self) -> None:
        if not hasattr(self, "quick_ref_input"):
            return
        typed_ref, packages = self._parse_quick_ref_text(self.quick_ref_input.text())
        ref = self._quick_selected_ref() or typed_ref
        if not ref:
            self.quick_status.setText("Tape une référence.")
            return
        product = self.db.get_product_by_ref(ref)
        if product is None:
            self.quick_status.setText(f"Référence inconnue: {ref}")
            return
        package_size = product.package_size or 0
        quantity_pieces = packages * package_size if package_size else packages
        line = self.resolver.line_from_product(
            product,
            quantity_pieces=quantity_pieces,
            package_count=packages,
            source="quick_invoice",
        )
        self.lines.append(line)
        self.current_order_source = "Facture rapide"
        self.current_order_key = ""
        self.current_order_path = None
        self._refresh_quick_invoice_table()
        self.quick_ref_input.clear()
        self.quick_suggestions.clear()
        self.quick_status.setText(f"{line.ref} ajouté: {packages} paquet(s), {line.quantity_pieces} pièce(s).")

    def _refresh_quick_invoice_table(self) -> None:
        if hasattr(self, "quick_table"):
            populate_lines_table(self.quick_table, self.lines, editable=True, source=QUICK_INVOICE_SOURCE)
            configure_line_table_autocomplete(self.quick_table, self.db)

    def _on_quick_item_changed(self, item: QTableWidgetItem) -> None:
        apply_line_table_item_change(self.quick_table, self.lines, self.db, QUICK_INVOICE_SOURCE, item, self.quick_status.setText)
        if hasattr(self, "quick_status"):
            self.quick_status.setText(_status_from_lines(self.lines) if self.lines else "Aucune ligne.")

    def _remove_quick_invoice_lines(self) -> None:
        if not hasattr(self, "quick_table"):
            return
        rows = sorted({item.row() for item in self.quick_table.selectedItems()}, reverse=True)
        for row in rows:
            if row < len(self.lines):
                del self.lines[row]
        self._refresh_quick_invoice_table()
        self.quick_status.setText(f"{len(self.lines)} ligne(s) dans la facture rapide.")

    def _clear_quick_invoice_lines(self) -> None:
        self.lines.clear()
        self.current_order_source = ""
        self.current_order_key = ""
        self.current_order_path = None
        self._refresh_quick_invoice_table()
        self.quick_status.setText("Facture rapide vidée.")

    def _inject_quick_invoice(self) -> None:
        if not self.lines:
            self.quick_status.setText("Aucune ligne à injecter.")
            return
        self.current_order_source = "Facture rapide"
        self.current_order_key = ""
        if self._prepare_injection():
            self.quick_status.setText("Injection envoyée pour la facture rapide.")

    def _new_product_draft(self) -> None:
        dialog = ProductDraftDialog(parent=self)
        if dialog.exec() != QDialog.Accepted or dialog.saved_product is None:
            return
        saved = self.db.upsert_product_draft(dialog.saved_product)
        self._refresh_products_page()
        QMessageBox.information(self, APP_NAME, f"Produit sauvegardé en local: {saved.ref}")

    def _edit_selected_product(self) -> None:
        product = self._selected_product()
        if product is None:
            QMessageBox.information(self, APP_NAME, "Aucun produit sélectionné.")
            return
        dialog = ProductDraftDialog(product, self)
        if dialog.exec() != QDialog.Accepted or dialog.saved_product is None:
            return
        saved = self.db.upsert_product_draft(dialog.saved_product)
        self._refresh_products_page()
        QMessageBox.information(self, APP_NAME, f"Produit sauvegardé en local: {saved.ref}")

    def _simulate_selected_product(self) -> None:
        product = self._selected_product()
        if product is None:
            QMessageBox.information(self, APP_NAME, "Aucun produit sélectionné.")
            return
        changes = self.db.product_change_preview(product)
        QMessageBox.information(
            self,
            APP_NAME,
            "Simulation locale uniquement.\n\n"
            + "\n".join(changes)
            + "\n\nAucune écriture Microstore ne sera lancée sans validation explicite.",
        )

    def _apply_selected_product_to_microstore(self) -> None:
        product = self._selected_product()
        if product is None:
            QMessageBox.information(self, APP_NAME, "Aucun produit sélectionné.")
            return
        if product.workflow_status not in {"to_create", "modified"}:
            QMessageBox.information(self, APP_NAME, "Ce produit n'a pas de modification locale à appliquer.")
            return
        changes = self.db.product_change_preview(product)
        writer = self._microstore_writer()
        if writer is None:
            return
        payload = writer.build_payload(product)
        if not self._confirm_microstore_write(payload, changes):
            return
        try:
            saved_product = writer.apply(product)
            self.db.upsert_products([saved_product])
        except MicrostoreWriteError as exc:
            self.db.log("microstore_write_error", f"{product.ref}: {exc}")
            QMessageBox.warning(self, APP_NAME, f"Écriture Microstore échouée:\n{exc}")
            return
        self._refresh_products_page()
        self.db.log("microstore_write", f"{payload.action}: {product.ref}")
        QMessageBox.information(self, APP_NAME, f"Produit appliqué à Microstore: {saved_product.ref}")

    def _set_selected_product_active(self, active: bool) -> None:
        product = self._selected_product()
        if product is None:
            QMessageBox.information(self, APP_NAME, "Aucun produit sélectionné.")
            return
        action_label = "réactiver" if active else "désactiver"
        microstore_id = product.raw.get("id") if isinstance(product.raw, dict) else None
        if not microstore_id:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Id Microstore absent. Synchronise d'abord la DB produits Microstore, puis réessaie.",
            )
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Confirmer: {action_label} {product.ref} dans Microstore ?",
        )
        if answer != QMessageBox.Yes:
            return
        writer = self._microstore_writer()
        if writer is None:
            return
        try:
            saved_product = writer.set_active(product, active)
            self.db.upsert_products([saved_product])
        except MicrostoreWriteError as exc:
            self.db.log("microstore_status_error", f"{product.ref}: {exc}")
            QMessageBox.warning(self, APP_NAME, f"Changement de statut Microstore échoué:\n{exc}")
            return
        self._refresh_products_page()
        self.db.log("microstore_status", f"{action_label}: {product.ref}")
        QMessageBox.information(self, APP_NAME, f"Produit {action_label} dans Microstore: {saved_product.ref}")

    def _microstore_writer(self) -> MicrostoreProductWriter | None:
        self._save_app_settings_silent()
        token = self.settings.microstore_api_token.strip()
        if not token and hasattr(self, "microstore_token"):
            token = self.microstore_token.text().strip()
        if not token:
            QMessageBox.warning(self, APP_NAME, "Token Microstore absent dans Réglages.")
            return None
        return MicrostoreProductWriter(MicrostoreConnector(token))

    def _confirm_microstore_write(self, payload: MicrostoreProductPayload, changes: list[str]) -> bool:
        payload_preview = json.dumps(payload.payload, ensure_ascii=False, indent=2)
        if len(payload_preview) > 2200:
            payload_preview = payload_preview[:2200] + "\n..."
        action = "Créer" if payload.action == "create" else "Modifier"
        text = (
            f"{action} le produit dans Microstore ?\n\n"
            + "\n".join(changes)
            + f"\n\nEndpoint: {payload.endpoint}\n\n"
            + payload_preview
        )
        return QMessageBox.question(self, APP_NAME, text) == QMessageBox.Yes

    def _portal_credentials(self, source: str) -> tuple[str, str] | None:
        if source == "eFashion":
            email = self.efashion_email.text().strip()
            password = self.efashion_password.text()
        elif source == "PFS":
            email = self.pfs_email.text().strip()
            password = self.pfs_password.text()
        else:
            raise ValueError(f"Source inconnue: {source}")
        if not email or not password:
            return None
        return email, password

    def _login_efashion(self, show_error: bool = True) -> bool:
        credentials = self._portal_credentials("eFashion")
        if not credentials:
            self.efashion_status.setText("eFashion: identifiants absents")
            return False
        email, password = credentials
        try:
            session = self.efashion_connector.login(email, password)
        except Exception as exc:
            self.efashion_status.setText(f"eFashion: erreur connexion ({exc})")
            if show_error:
                QMessageBox.critical(self, APP_NAME, f"Connexion eFashion impossible: {exc}")
            return False
        self.efashion_status.setText(f"eFashion: connecte ({session.user_label})")
        self.settings.portal_sessions["eFashion"] = _session_to_settings(session)
        save_settings(self.settings)
        self.db.log("portal_login", "Connexion eFashion OK")
        return True

    def _login_pfs(self, show_error: bool = True) -> bool:
        credentials = self._portal_credentials("PFS")
        if not credentials:
            self.pfs_status.setText("PFS: identifiants absents")
            return False
        email, password = credentials
        try:
            session = self.pfs_connector.login(email, password)
        except Exception as exc:
            self.pfs_status.setText(f"PFS: erreur connexion ({exc})")
            if show_error:
                QMessageBox.critical(self, APP_NAME, f"Connexion PFS impossible: {exc}")
            return False
        self.pfs_status.setText(f"PFS: connecte ({session.user_label})")
        self.settings.portal_sessions["PFS"] = _session_to_settings(session)
        save_settings(self.settings)
        self.db.log("portal_login", "Connexion PFS OK")
        return True

    def _test_portal_connection(self, source: str) -> None:
        self._save_app_settings_silent()
        if source == "eFashion":
            ok = self._login_efashion(show_error=False)
            label = self.efashion_status.text()
        elif source == "PFS":
            ok = self._login_pfs(show_error=False)
            label = self.pfs_status.text()
        else:
            return
        if ok:
            QMessageBox.information(self, APP_NAME, label)
        else:
            QMessageBox.warning(self, APP_NAME, label)

    def _reset_portal_sessions(self) -> None:
        self.settings.portal_sessions = {}
        save_settings(self.settings)
        self.efashion_status.setText("eFashion: session réinitialisée")
        self.pfs_status.setText("PFS: session réinitialisée")
        QMessageBox.information(self, APP_NAME, "Sessions eFashion/PFS réinitialisées. Les emails et mots de passe restent sauvegardés.")

    def _load_cached_orders(self) -> None:
        self.portal_summaries.clear()
        self.portal_details.clear()
        self.portal_status_cache = self.db.list_cached_order_statuses()
        for summary in self.db.list_cached_order_summaries():
            key = summary.order_number or summary.order_id
            cache_key = (summary.source, key)
            self.portal_summaries[cache_key] = summary
            detail = self.db.get_cached_order(summary.source, key)
            if detail:
                self.portal_details[cache_key] = detail
                edited_lines = self.db.get_order_line_edits(summary.source, key)
                self.portal_status_cache[cache_key] = _status_from_lines(edited_lines or self._lines_from_portal_order(detail))
        self._apply_order_filters()

    def _start_sync(self, sources: list[str]) -> None:
        requested_sources = list(sources)
        is_sync_all = set(requested_sources) == {"Microstore", "eFashion", "PFS"}
        if is_sync_all and self.sync_threads:
            if hasattr(self, "sync_summary"):
                self.sync_summary.setText("Synchronisation tout impossible: une synchro est deja en cours.")
            return
        if self.sync_all_active:
            if hasattr(self, "sync_summary"):
                self.sync_summary.setText("Synchronisation tout en cours.")
            return
        self._save_app_settings_silent()
        sources = self._expand_smart_sync_sources(sources)
        runnable_sources: list[str] = []
        if "MicrostoreProducts" in sources:
            if self.microstore_token.text().strip():
                runnable_sources.append("MicrostoreProducts")
            else:
                self.microstore_status.setText("Microstore produits: token absent")
        if "Microstore" in sources:
            if self.microstore_token.text().strip():
                runnable_sources.append("Microstore")
            else:
                self.microstore_status.setText("Microstore: token absent")
        if "eFashion" in sources:
            if self._portal_credentials("eFashion"):
                runnable_sources.append("eFashion")
            else:
                self.efashion_status.setText("eFashion: identifiants absents")
        if "PFS" in sources:
            if self._portal_credentials("PFS"):
                runnable_sources.append("PFS")
            else:
                self.pfs_status.setText("PFS: identifiants absents")
        runnable_sources = [source for source in runnable_sources if not self._sync_source_is_running(source)]
        if not runnable_sources:
            if hasattr(self, "sync_summary"):
                self.sync_summary.setText("Aucune source disponible a synchroniser, ou source deja en cours.")
            return
        for source in runnable_sources:
            self._on_sync_progress(source, 0, "en attente")

        self.sync_active_sources.update(runnable_sources)
        if is_sync_all:
            self.sync_all_active = True
        for source in runnable_sources:
            button = self.sync_buttons.get(source)
            if button:
                button.setEnabled(False)
            if source in {"Microstore", "MicrostoreProducts"}:
                for linked_source in ("Microstore", "MicrostoreProducts"):
                    linked_button = self.sync_buttons.get(linked_source)
                    if linked_button:
                        linked_button.setEnabled(False)
        if "all" in self.sync_buttons:
            self.sync_buttons["all"].setEnabled(False)
        if hasattr(self, "cancel_sync_button"):
            self.cancel_sync_button.setEnabled(True)
        if hasattr(self, "sync_summary"):
            self.sync_summary.setText("Synchronisation lancee.")

        thread = QThread(self)
        worker = SyncWorker(
            runnable_sources,
            self.microstore_token.text().strip(),
            self.microstore_days.value(),
            self.portal_order_limit.value(),
            self.efashion_email.text().strip(),
            self.efashion_password.text(),
            self.pfs_email.text().strip(),
            self.pfs_password.text(),
            self.settings.portal_sessions,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_sync_progress)
        worker.source_finished.connect(self._on_sync_source_finished)
        worker.source_error.connect(self._on_sync_source_error)
        worker.all_finished.connect(
            lambda result, t=thread, src=set(runnable_sources), all_sync=is_sync_all: self.sync_completion_relay.finished.emit(result, t, src, all_sync)
        )
        worker.all_finished.connect(thread.quit)
        worker.all_finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda t=thread: self._cleanup_sync_thread(t))
        self.sync_threads.append(thread)
        self.sync_workers.append(worker)
        self.sync_thread_sources[thread] = set(runnable_sources)
        self.sync_thread_workers[thread] = worker
        thread.start()

    def _sync_source_is_running(self, source: str) -> bool:
        if source in {"Microstore", "MicrostoreProducts"}:
            return bool(self.sync_active_sources.intersection({"Microstore", "MicrostoreProducts"}))
        return source in self.sync_active_sources

    def _expand_smart_sync_sources(self, sources: list[str]) -> list[str]:
        expanded = list(sources)
        if "Microstore" not in expanded or "MicrostoreProducts" in expanded:
            return expanded
        latest = self.db.latest_product_import()
        resync_hours = self.settings.microstore_product_resync_hours
        if should_sync_microstore_products(latest, resync_hours):
            return ["MicrostoreProducts", *expanded]
        if hasattr(self, "microstore_status"):
            self.microstore_status.setText(
                f"Microstore produits: déjà à jour ({_display_date(latest or '')})"
            )
        return expanded

    def _on_sync_progress(self, source: str, percent: int, message: str) -> None:
        display_source = "Microstore produits" if source == "MicrostoreProducts" else source
        label = f"{display_source}: {percent}% - {message}"
        if source in {"Microstore", "MicrostoreProducts"}:
            self.microstore_status.setText(label)
        elif source == "eFashion":
            self.efashion_status.setText(label)
        elif source == "PFS":
            self.pfs_status.setText(label)
        bar = self.sync_progress_bars.get("Microstore" if source == "MicrostoreProducts" else source)
        if bar:
            bar.setValue(percent)

    def _on_sync_source_finished(self, source: str, summary: dict) -> None:
        orders = int(summary.get("orders") or 0)
        products = int(summary.get("products") or 0)
        clients = int(summary.get("clients") or 0)
        cancelled = bool(summary.get("cancelled"))
        if cancelled:
            message = f"annule - {orders} commandes sauvegardees"
        elif source == "MicrostoreProducts":
            message = f"{products} produits sauvegardes"
        elif source == "Microstore":
            message = f"{orders} commandes, {clients} clients"
        else:
            user = summary.get("user") or ""
            message = f"{orders} commandes sauvegardees" + (f" ({user})" if user else "")
        self._on_sync_progress(source, 100, message)

    def _on_sync_source_error(self, source: str, message: str) -> None:
        self._on_sync_progress(source, 100, f"erreur - {message}")

    def _on_sync_all_finished(self, result: dict, thread: QThread, finished_sources: set[str], was_sync_all: bool) -> None:
        sessions = result.get("sessions")
        if isinstance(sessions, dict):
            self.settings.portal_sessions.update({str(source): payload for source, payload in sessions.items() if isinstance(payload, dict)})
            save_settings(self.settings)
        self.sync_active_sources.difference_update(finished_sources)
        if was_sync_all:
            self.sync_all_active = False
        for source in finished_sources:
            button = self.sync_buttons.get(source)
            if button:
                button.setEnabled(True)
            if source in {"Microstore", "MicrostoreProducts"}:
                if not self.sync_active_sources.intersection({"Microstore", "MicrostoreProducts"}):
                    for linked_source in ("Microstore", "MicrostoreProducts"):
                        linked_button = self.sync_buttons.get(linked_source)
                        if linked_button:
                            linked_button.setEnabled(True)
        if "all" in self.sync_buttons:
            self.sync_buttons["all"].setEnabled(not self.sync_active_sources and not self.sync_all_active)
        if hasattr(self, "cancel_sync_button"):
            self.cancel_sync_button.setEnabled(bool(self.sync_active_sources))
        self._load_cached_orders()
        self._refresh_status()
        self._refresh_missing_types()
        self._refresh_products_page()
        self._refresh_clients_page()
        if hasattr(self, "sync_summary"):
            parts = []
            for source, payload in result.get("sources", {}).items():
                orders = int(payload.get("orders") or 0)
                products = int(payload.get("products") or 0)
                clients = int(payload.get("clients") or 0)
                user = payload.get("user") or ""
                if payload.get("cancelled"):
                    label = "Microstore produits" if source == "MicrostoreProducts" else source
                    saved = f"{products} produits" if source == "MicrostoreProducts" else f"{orders} sauvegardees"
                    parts.append(f"{label}: annule ({saved})")
                elif source == "MicrostoreProducts":
                    parts.append(f"Microstore produits: {products} produits")
                elif source == "Microstore":
                    parts.append(f"Microstore: {orders} commandes, {clients} clients")
                else:
                    parts.append(f"{source}: {orders} commandes" + (f" ({user})" if user else ""))
            for source, message in result.get("errors", {}).items():
                parts.append(f"{source}: erreur")
            self.sync_summary.setText(" | ".join(parts) if parts else "Synchronisation terminee.")

    def _cleanup_sync_thread(self, thread: QThread) -> None:
        self.sync_threads = [item for item in self.sync_threads if item is not thread]
        worker = self.sync_thread_workers.pop(thread, None)
        if worker is not None:
            self.sync_workers = [item for item in self.sync_workers if item is not worker]
        self.sync_thread_sources.pop(thread, None)

    def _cancel_sync(self) -> None:
        for worker in list(self.sync_workers):
            worker.cancel()
        if hasattr(self, "sync_summary"):
            self.sync_summary.setText("Annulation demandee. Fin de l'etape en cours...")
        if hasattr(self, "cancel_sync_button"):
            self.cancel_sync_button.setEnabled(False)

    def _sync_microstore_api_silent(self) -> None:
        self._start_sync(["Microstore"])

    def _sync_microstore_api(self, show_message: bool = True) -> int | None:
        token = self.microstore_token.text().strip()
        if not token:
            self.microstore_status.setText("Microstore: token absent")
            if show_message:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "Token Microstore absent.\n\nCopie la valeur localStorage admin_token depuis Chrome connecte a web.mc.app, puis colle-la dans Reglages > Synchronisation.",
                )
            return None
        self._start_sync(["Microstore"])
        return None

    def _refresh_orders(self) -> None:
        self._start_sync(["Microstore", "eFashion", "PFS"])

    def _sync_all_sources(self) -> None:
        self._start_sync(["Microstore", "eFashion", "PFS"])

    def _sync_portal_orders(self) -> None:
        self._sync_all_sources()

    def _sync_source_orders(self, source: str, summaries: list[PortalOrderSummary]) -> int:
        count = 0
        for summary in summaries:
            if not summary.order_id:
                continue
            key = summary.order_number or summary.order_id
            cache_key = (source, key)
            self.portal_summaries[cache_key] = summary
            try:
                detail = self._fetch_portal_order(source, key)
                edited_lines = self.db.get_order_line_edits(source, key)
                self.portal_status_cache[cache_key] = _status_from_lines(edited_lines or self._lines_from_portal_order(detail))
            except Exception:
                self.portal_status_cache[cache_key] = STATUS_ERROR
            count += 1
        self.db.log("portal_sync", f"{count} commande(s) {source} synchronisee(s)")
        return count

    def _fetch_portal_order(self, source: str, key: str) -> PortalOrder:
        cache_key = (source, key)
        if cache_key in self.portal_details:
            return self.portal_details[cache_key]
        summary = self.portal_summaries.get(cache_key)
        if not summary:
            raise ValueError(f"Commande {source} {key} introuvable.")
        if source == "eFashion":
            detail = self.efashion_connector.get_order(summary.order_id)
        elif source == "Microstore":
            detail = self.microstore_connector.get_order(summary.order_id)
        elif source == "PFS":
            detail = self.pfs_connector.get_order(summary.order_id)
        else:
            raise ValueError(f"Source portail inconnue: {source}")
        self.portal_details[cache_key] = detail
        return detail

    def _lines_from_portal_order(self, order: PortalOrder) -> list[InvoiceLine]:
        lines = self.resolver.lines_from_portal_lines(order.lines, source=order.source)
        if order.source == QUICK_INVOICE_SOURCE:
            for invoice_line, portal_line in zip(lines, order.lines, strict=False):
                if portal_line.raw.get("sage_code"):
                    invoice_line.sage_code = str(portal_line.raw.get("sage_code") or "")
                if portal_line.description:
                    invoice_line.description = portal_line.description
                if portal_line.raw.get("product_id") not in (None, ""):
                    try:
                        invoice_line.product_id = int(portal_line.raw["product_id"])
                    except (TypeError, ValueError):
                        pass
                invoice_line.validate()
        return lines

    def _prepare_injection(self) -> bool:
        if not self.lines:
            QMessageBox.information(self, APP_NAME, "Aucune ligne a injecter.")
            return False
        self._save_app_settings_silent()
        for line in self.lines:
            line.validate()
        blocked = [line for line in self.lines if line.validation_status != "ok"]
        if blocked:
            refs = ", ".join(line.ref for line in blocked[:8])
            QMessageBox.warning(
                self,
                APP_NAME,
                "Certaines lignes sont bloquees. Corrige les mappings ou supprime-les en detail commande.\n\n" + refs,
            )
            return False
        try:
            path = write_injection_queue(self.lines, self.settings)
            self.db.log("injection_prepare", f"{len(self.lines)}/{len(self.lines)} lignes preparees dans fichier temporaire: {path}")
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return False
        if is_windows():
            try:
                process = launch_autohotkey(self.settings, path)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Lancement AutoHotkey impossible:\n\n{exc}\n\nFile temporaire creee:\n{path}")
                return False
            control_path = path.with_suffix(".control")
            self.injection_control_dialog = InjectionControlDialog(process, control_path, self)
            self.injection_control_dialog.show()
        if self.current_order_source and self.current_order_key:
            self.db.set_order_status(self.current_order_source, self.current_order_key, STATUS_INJECTED)
            if self.current_order_path is None:
                self.portal_status_cache[(self.current_order_source, self.current_order_key)] = STATUS_INJECTED
                self._apply_order_filters()
            else:
                self._refresh_order_folder()
        if is_windows():
            self.statusBar().showMessage("Injection envoyée à AutoHotkey. Vérifie Sage visuellement.", 5000)
        else:
            QMessageBox.information(self, APP_NAME, f"File temporaire d'injection creee:\n{path}")
        return True

    def _import_products(self) -> None:
        path = self._pick_excel_file("Choisir export produits Microstore")
        if not path:
            return
        self._import_products_path(path)

    def _import_products_path(self, path: Path) -> None:
        try:
            result = import_products(path)
            count = self.db.upsert_products(result.rows)  # type: ignore[arg-type]
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self.settings.last_product_file_path = str(path)
        save_settings(self.settings)
        self._refresh_status()
        self._refresh_missing_types()
        self._refresh_product_folder()
        self._refresh_order_folder()
        QMessageBox.information(self, APP_NAME, f"{count} references importees.\n" + "\n".join(result.warnings[:10]))

    def _import_order(self) -> None:
        path = self._pick_excel_file("Choisir commande Microstore")
        if not path:
            return
        self._import_order_path(path)

    def _load_order_lines(self, path: Path, log: bool = True) -> list[InvoiceLine]:
        result = import_order(path)
        lines = [self.resolver.line_from_order_row(row) for row in result.rows]  # type: ignore[arg-type]
        if log:
            self.db.log("order_import", f"{len(lines)} lignes commande importees depuis {path.name}")
        return lines

    def _import_order_path(self, path: Path) -> None:
        try:
            self.lines = self._load_order_lines(path)
            self.current_order_path = path
            self.current_order_source = _source_for_path(path, self.settings.order_folder_path)
            self.current_order_key = path.stem
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._open_order_detail(path, self.lines, self.current_order_source, self.current_order_key)

    def _selected_order_path(self) -> Path | None:
        rows = sorted({item.row() for item in self.order_table.selectedItems()})
        if not rows:
            return None
        item = self.order_table.item(rows[0], 0)
        if item.data(ROLE_KIND) != "file":
            return None
        path_value = item.data(ROLE_PAYLOAD)
        return Path(path_value)

    def _selected_order_source_key(self) -> tuple[str, str]:
        rows = sorted({item.row() for item in self.order_table.selectedItems()})
        if not rows:
            return "", ""
        item = self.order_table.item(rows[0], 0)
        return item.data(ROLE_SOURCE), item.data(ROLE_KEY)

    def _selected_order_kind(self) -> str:
        rows = sorted({item.row() for item in self.order_table.selectedItems()})
        if not rows:
            return ""
        return str(self.order_table.item(rows[0], 0).data(ROLE_KIND) or "")

    def _selected_order_web_url(self) -> str:
        kind = self._selected_order_kind()
        if not kind:
            return ""
        source, key = self._selected_order_source_key()
        order_id = ""
        if kind == "portal":
            summary = self.portal_summaries.get((source, key))
            order_id = summary.order_id if summary else ""
        return _order_web_url(source, order_id, key, self.settings.microstore_api_token)

    def _open_selected_order_web_page(self) -> None:
        url = self._selected_order_web_url()
        if not url:
            QMessageBox.information(self, APP_NAME, "Aucune commande selectionnee.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _copy_selected_order_web_link(self) -> None:
        url = self._selected_order_web_url()
        if not url:
            QMessageBox.information(self, APP_NAME, "Aucune commande selectionnee.")
            return
        QApplication.clipboard().setText(url)
        if hasattr(self, "status_label"):
            self.status_label.setText("Lien commande copie dans le presse-papiers.")

    def _save_order_line_edits(self, source: str, key: str, lines: list[InvoiceLine]) -> None:
        status = _status_from_lines(lines)
        self.db.save_order_line_edits(source, key, lines, status)
        self.portal_status_cache[(source, key)] = status
        self._load_cached_orders()
        self._refresh_status()

    def _reset_order_line_edits(self, source: str, key: str, original_order: PortalOrder) -> list[InvoiceLine]:
        self.db.clear_order_line_edits(source, key)
        lines = self._lines_from_portal_order(original_order)
        status = _status_from_lines(lines)
        summary = PortalOrderSummary(
            source=original_order.source,
            order_id=original_order.order_id,
            order_number=original_order.order_number,
            customer=original_order.customer,
            created_at=original_order.created_at,
            status=original_order.status,
            total_amount=original_order.total_amount,
            raw=original_order.raw,
        )
        self.db.upsert_cached_order(summary, original_order, status)
        self.db.set_order_status(source, key, status)
        self.portal_status_cache[(source, key)] = status
        self._load_cached_orders()
        self._refresh_status()
        return lines

    def _reset_file_order_line_edits(self, source: str, key: str, path: Path) -> list[InvoiceLine]:
        self.db.clear_order_line_edits(source, key)
        lines = self._load_order_lines(path)
        status = _status_from_lines(lines)
        self.db.set_order_status(source, key, status)
        self._refresh_order_folder()
        self._refresh_status()
        return lines

    def _open_selected_order_detail(self) -> None:
        kind = self._selected_order_kind()
        if not kind:
            QMessageBox.information(self, APP_NAME, "Aucune commande selectionnee.")
            return
        source, key = self._selected_order_source_key()
        try:
            if kind == "portal":
                order = self._fetch_portal_order(source, key)
                lines = self._lines_from_portal_order(order)
                lines = lines_with_saved_order_edits(self.db, source, key, lines)
                self._open_portal_order_detail(order, lines)
                return
            path = self._selected_order_path()
            if not path:
                raise ValueError("Commande fichier introuvable.")
            lines = self._load_order_lines(path)
            lines = lines_with_saved_order_edits(self.db, source, key, lines)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._open_order_detail(path, lines, source, key)

    def _open_portal_order_detail(self, order: PortalOrder, lines: list[InvoiceLine]) -> None:
        key = order.order_number or order.order_id
        status = self._status_for_portal_order(order.source, key, order)
        summary = {
            "source": order.source,
            "number": key,
            "customer": order.customer,
            "date": _display_date(order.created_at),
            "total": _money_label(order.total_amount),
            "status": status,
            "web_url": _order_web_url(order.source, order.order_id, key, self.settings.microstore_api_token),
            "extra_fields": _order_extra_fields(order),
        }
        dialog = OrderDetailDialog(
            lines,
            summary,
            self.db,
            autosave_callback=lambda updated_lines, s=order.source, k=key: self._save_order_line_edits(s, k, updated_lines),
            reset_callback=lambda s=order.source, k=key, original=order: self._reset_order_line_edits(s, k, original),
            parent=self,
        )
        dialog.exec()
        self.lines = dialog.lines
        self.current_order_path = None
        self.current_order_source = order.source
        self.current_order_key = key
        self.portal_status_cache[(order.source, key)] = _status_from_lines(self.lines)
        self._apply_order_filters()
        if dialog.inject_requested:
            self._prepare_injection()

    def _open_order_detail(self, path: Path, lines: list[InvoiceLine], source: str, key: str) -> None:
        order_file = self._order_file_for_path(path)
        status = self._status_for_order(path, source, key, order_file)
        summary = {
            "source": source,
            "number": key,
            "customer": self._customer_label(order_file) if order_file else "",
            "date": order_file.order_date if order_file else "",
            "total": _money_label(order_file.total_amount) if order_file else "",
            "status": status,
            "web_url": _order_web_url(source, "", key, self.settings.microstore_api_token),
        }
        dialog = OrderDetailDialog(
            lines,
            summary,
            self.db,
            autosave_callback=lambda updated_lines, s=source, k=key: self._save_order_line_edits(s, k, updated_lines),
            reset_callback=lambda s=source, k=key, p=path: self._reset_file_order_line_edits(s, k, p),
            parent=self,
        )
        dialog.exec()
        self.lines = dialog.lines
        self.current_order_path = path
        self.current_order_source = source
        self.current_order_key = key
        self._refresh_order_folder()
        if dialog.inject_requested:
            self._prepare_injection()

    def _inject_selected_order_from_folder(self) -> None:
        kind = self._selected_order_kind()
        if not kind:
            QMessageBox.information(self, APP_NAME, "Aucune commande selectionnee.")
            return
        source, key = self._selected_order_source_key()
        try:
            if kind == "portal":
                order = self._fetch_portal_order(source, key)
                self.lines = self._lines_from_portal_order(order)
                self.lines = lines_with_saved_order_edits(self.db, source, key, self.lines)
                self.current_order_path = None
            else:
                path = self._selected_order_path()
                if not path:
                    raise ValueError("Commande fichier introuvable.")
                self.lines = self._load_order_lines(path)
                self.lines = lines_with_saved_order_edits(self.db, source, key, self.lines)
                self.current_order_path = path
            self.current_order_source = source
            self.current_order_key = key
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._prepare_injection()

    def _mark_selected_order_done(self) -> None:
        kind = self._selected_order_kind()
        if not kind:
            QMessageBox.information(self, APP_NAME, "Aucune commande selectionnee.")
            return
        source, key = self._selected_order_source_key()
        self.db.set_order_status(source, key, STATUS_DONE)
        if kind == "portal":
            self.portal_status_cache[(source, key)] = STATUS_DONE
            self._apply_order_filters()
        else:
            self._refresh_order_folder()

    def _delete_selected_order(self) -> None:
        kind = self._selected_order_kind()
        if not kind:
            QMessageBox.information(self, APP_NAME, "Aucune commande sélectionnée.")
            return
        source, key = self._selected_order_source_key()
        if kind != "portal":
            QMessageBox.information(self, APP_NAME, "La suppression depuis la liste concerne le cache app. Les fichiers manuels restent sur disque.")
            return
        if QMessageBox.question(self, APP_NAME, f"Supprimer {source} {key} du cache de l'application ?") != QMessageBox.Yes:
            return
        self.db.delete_cached_order(source, key)
        self._load_cached_orders()
        self._refresh_status()

    def _clear_all_orders(self) -> None:
        if QMessageBox.question(self, APP_NAME, "Vider toutes les commandes synchronisées du cache de l'application ?") != QMessageBox.Yes:
            return
        removed = self.db.clear_cached_orders()
        self._load_cached_orders()
        self._refresh_status()
        QMessageBox.information(self, APP_NAME, f"{removed} commande(s) supprimée(s) du cache.")

    def _open_selected_order_client(self) -> None:
        kind = self._selected_order_kind()
        if not kind:
            QMessageBox.information(self, APP_NAME, "Aucune commande sélectionnée.")
            return
        source, key = self._selected_order_source_key()
        if source != "Microstore":
            QMessageBox.information(self, APP_NAME, "Les fiches clients intégrées sont disponibles d'abord pour Microstore.")
            return
        customer = ""
        if kind == "portal":
            summary = self.portal_summaries.get((source, key))
            customer = summary.customer if summary else ""
        else:
            path = self._selected_order_path()
            order_file = self._order_file_for_path(path) if path else None
            customer = self._customer_label(order_file) if order_file else ""
        client = self.db.find_client("Microstore", customer)
        if client is None:
            QMessageBox.information(self, APP_NAME, "Fiche client Microstore introuvable dans le cache. Lance une synchronisation Microstore.")
            return
        self._open_client_detail(client)

    def _choose_order_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choisir dossier commandes Microstore", self.order_folder_input.text())
        if not folder:
            return
        self.order_folder_input.setText(folder)
        self.settings.order_folder_path = folder
        save_settings(self.settings)
        self._refresh_order_folder()

    def _choose_product_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choisir dossier MS_IMPORT", self.product_folder_input.text())
        if not folder:
            return
        self.product_folder_input.setText(folder)
        self.settings.product_folder_path = folder
        save_settings(self.settings)
        self._refresh_product_folder()

    def _refresh_product_folder(self) -> None:
        if not hasattr(self, "product_export_status"):
            return
        folder = self.product_folder_input.text().strip()
        self.settings.product_folder_path = folder
        self.detected_product_export = latest_product_export(folder) if folder else None
        if not self.detected_product_export:
            self.product_export_status.setText("Aucune BDD Microstore detectee.")
            return
        export = self.detected_product_export
        imported_marker = ""
        if self.settings.last_product_file_path and Path(self.settings.last_product_file_path) == export.path:
            imported_marker = " deja importee"
        elif self.settings.last_product_file_path:
            imported_marker = " nouvelle BDD disponible"
        status = (
            f"{export.path.name} - {export.modified_at_label} - "
            f"{export.product_count} refs"
            f"{' - ' + str(export.warning_count) + ' warnings' if export.warning_count else ''}"
            f"{' - erreur: ' + export.error if export.error else ''}"
            f"{imported_marker}"
        )
        self.product_export_status.setText(status)

    def _import_detected_product_export(self) -> None:
        if not self.detected_product_export:
            QMessageBox.information(self, APP_NAME, "Aucune BDD Microstore detectee.")
            return
        if self.detected_product_export.error:
            QMessageBox.warning(self, APP_NAME, f"BDD detectee invalide: {self.detected_product_export.error}")
            return
        self._import_products_path(self.detected_product_export.path)

    def _refresh_order_folder(self) -> None:
        if not hasattr(self, "order_table"):
            return
        folder = self.order_folder_input.text().strip() if hasattr(self, "order_folder_input") else self.settings.order_folder_path
        self.settings.order_folder_path = folder
        self.detected_order_files = [] if self.microstore_token.text().strip() else (list_order_files(folder) if folder else [])
        self.order_status_cache = {}
        for order_file in self.detected_order_files[:100]:
            source = "Microstore"
            key = _order_key(order_file)
            self.order_status_cache[order_file.path] = self._status_for_order(order_file.path, source, key, order_file)
        self._apply_order_filters()

    def _apply_order_filters(self) -> None:
        if not hasattr(self, "order_table"):
            return
        self.order_table.setUpdatesEnabled(False)
        self.order_table.blockSignals(True)
        search = self.order_search.text().strip().lower()
        source_filter = self.source_filter.currentText()
        status_filter = self.status_filter.currentText()
        date_filter = self.date_filter.currentText()

        file_rows: list[OrderFile] = []
        for order_file in self.detected_order_files[:100]:
            source = "Microstore"
            key = _order_key(order_file)
            status = self.order_status_cache.get(order_file.path, STATUS_ERROR if order_file.error else STATUS_READY)
            haystack = " ".join(
                [
                    source,
                    key,
                    self._customer_label(order_file),
                    order_file.customer_email,
                    order_file.customer_phone,
                    order_file.order_date,
                ]
            ).lower()
            if search and search not in haystack:
                continue
            if source_filter != "Toutes" and source_filter != source:
                continue
            if status_filter != "Tous" and status_filter != status:
                continue
            if not self._matches_date_filter(order_file.modified_at, date_filter):
                continue
            file_rows.append(order_file)

        portal_rows: list[PortalOrderSummary] = []
        for (source, key), summary in self.portal_summaries.items():
            status = self._status_for_portal_order(source, key)
            haystack = " ".join([source, key, summary.order_id, summary.customer, summary.created_at, summary.status]).lower()
            if search and search not in haystack:
                continue
            if source_filter != "Toutes" and source_filter != source:
                continue
            if status_filter != "Tous" and status_filter != status:
                continue
            if not self._matches_date_filter(_timestamp_from_iso(summary.created_at), date_filter):
                continue
            portal_rows.append(summary)

        try:
            portal_rows.sort(key=lambda item: _timestamp_from_iso(item.created_at), reverse=True)
            self.order_table.setRowCount(len(file_rows) + len(portal_rows))
            row = 0
            for order_file in file_rows:
                source = "Microstore"
                key = _order_key(order_file)
                status = self.order_status_cache.get(order_file.path, STATUS_ERROR if order_file.error else STATUS_READY)
                edited_lines = self.db.get_order_line_edits(source, key)
                line_count = len(edited_lines) if edited_lines is not None else order_file.line_count
                values = [
                    source,
                    key,
                    self._customer_label(order_file),
                    _display_date(order_file.order_date) or order_file.modified_at_label,
                    str(line_count) if not order_file.error else "Erreur",
                    order_file.total_amount_label if not order_file.error else "",
                    status,
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(ROLE_KIND, "file")
                    item.setData(ROLE_SOURCE, source)
                    item.setData(ROLE_KEY, key)
                    item.setData(ROLE_PAYLOAD, str(order_file.path))
                    self.order_table.setItem(row, col, item)
                row += 1
            for summary in portal_rows:
                key = summary.order_number or summary.order_id
                detail = self.portal_details.get((summary.source, key))
                edited_lines = self.db.get_order_line_edits(summary.source, key)
                line_count = len(edited_lines) if edited_lines is not None else (len(detail.lines) if detail else "")
                status = self._status_for_portal_order(summary.source, key, detail)
                values = [
                    summary.source,
                    key,
                    summary.customer,
                    _display_date(summary.created_at),
                    str(line_count),
                    _money_label(summary.total_amount),
                    status,
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(ROLE_KIND, "portal")
                    item.setData(ROLE_SOURCE, summary.source)
                    item.setData(ROLE_KEY, key)
                    item.setData(ROLE_PAYLOAD, summary.order_id)
                    self.order_table.setItem(row, col, item)
                row += 1
        finally:
            self.order_table.blockSignals(False)
            self.order_table.setUpdatesEnabled(True)

    def _matches_date_filter(self, modified_at: float, date_filter: str) -> bool:
        if date_filter == "Toutes":
            return True
        modified = datetime.fromtimestamp(modified_at)
        now = datetime.now()
        if date_filter == "Aujourd'hui":
            return modified.date() == now.date()
        if date_filter == "7 jours":
            return modified >= now - timedelta(days=7)
        if date_filter == "30 jours":
            return modified >= now - timedelta(days=30)
        return True

    def _status_for_order(self, path: Path, source: str, key: str, order_file: OrderFile | None = None) -> str:
        if order_file and order_file.error:
            return STATUS_ERROR
        stored = self.db.get_order_status(source, key)
        if stored in TERMINAL_STATUSES:
            return stored
        edited_lines = self.db.get_order_line_edits(source, key)
        if edited_lines is not None:
            return stored or _status_from_lines(edited_lines)
        try:
            lines = self._load_order_lines(path, log=False)
        except Exception:
            return STATUS_ERROR
        computed = _status_from_lines(lines)
        return stored or computed

    def _status_for_portal_order(self, source: str, key: str, order: PortalOrder | None = None) -> str:
        stored = self.db.get_order_status(source, key)
        if stored in TERMINAL_STATUSES:
            return stored
        edited_lines = self.db.get_order_line_edits(source, key)
        if edited_lines is not None:
            status = _status_from_lines(edited_lines)
            self.portal_status_cache[(source, key)] = status
            return stored or status
        if (source, key) in self.portal_status_cache:
            return stored or self.portal_status_cache[(source, key)]
        if order is not None:
            status = _status_from_lines(self._lines_from_portal_order(order))
            self.portal_status_cache[(source, key)] = status
            return stored or status
        return stored or STATUS_READY

    def _customer_label(self, order_file: OrderFile | None) -> str:
        if not order_file:
            return ""
        customer = order_file.customer_name or order_file.customer_company or "Client inconnu"
        city = " ".join(part for part in (order_file.customer_zip, order_file.customer_city) if part)
        return f"{customer} - {city}" if city else customer

    def _order_file_for_path(self, path: Path) -> OrderFile | None:
        for order_file in self.detected_order_files:
            if order_file.path == path:
                return order_file
        return None

    def _pick_excel_file(self, title: str) -> Path | None:
        filename, _ = QFileDialog.getOpenFileName(self, title, "", "Excel (*.xlsx *.xlsm *.xls)")
        return Path(filename) if filename else None

    def _open_mappings_dialog(self) -> None:
        dialog = SageMappingsDialog(self.db, self)
        dialog.exec()
        self._refresh_missing_types()
        self._apply_order_filters()

    def _refresh_mappings_table(self) -> None:
        if not hasattr(self, "mapping_table"):
            return
        mappings = self.db.list_mappings(active_only=False)
        self.mapping_table.setRowCount(len(mappings))
        for row, mapping in enumerate(mappings):
            values = [
                mapping.microstore_type,
                mapping.sage_code,
                "Oui" if mapping.is_active else "Non",
            ]
            for col, value in enumerate(values):
                self.mapping_table.setItem(row, col, QTableWidgetItem(value))
        self.mapping_table.resizeRowsToContents()

    def _clear_mapping_form(self) -> None:
        self.mapping_table.clearSelection()
        self.mapping_type.clear()
        self.mapping_code.clear()

    def _save_mapping(self) -> None:
        mapping = SageMapping(
            microstore_type=self.mapping_type.text().strip(),
            sage_code=self.mapping_code.text().strip(),
            sage_label=self.mapping_code.text().strip().upper(),
        )
        if not mapping.microstore_type or not mapping.sage_code:
            QMessageBox.warning(self, APP_NAME, "Categorie et code Sage sont obligatoires.")
            return
        self.db.upsert_mapping(mapping)
        self._refresh_mappings_table()
        self._refresh_missing_types()
        self._refresh_order_folder()

    def _delete_mapping(self) -> None:
        rows = sorted({item.row() for item in self.mapping_table.selectedItems()})
        if not rows:
            QMessageBox.information(self, APP_NAME, "Selectionne un mapping a desactiver.")
            return
        microstore_type = self.mapping_table.item(rows[0], 0).text()
        self.db.deactivate_mapping(microstore_type)
        self._clear_mapping_form()
        self._refresh_mappings_table()
        self._refresh_missing_types()
        self._refresh_order_folder()

    def _restore_default_mappings(self) -> None:
        restored = self.db.restore_default_mappings()
        self._refresh_mappings_table()
        self._refresh_missing_types()
        self._refresh_order_folder()
        QMessageBox.information(self, APP_NAME, f"{restored} mapping(s) par defaut restaure(s).")

    def _load_selected_mapping(self) -> None:
        rows = sorted({item.row() for item in self.mapping_table.selectedItems()})
        if not rows:
            return
        row = rows[0]
        self.mapping_type.setText(self.mapping_table.item(row, 0).text())
        self.mapping_code.setText(self.mapping_table.item(row, 1).text())

    def _save_app_settings(self) -> None:
        self._save_app_settings_silent()
        QMessageBox.information(self, APP_NAME, "Reglages sauvegardes.")

    def _save_app_settings_silent(self) -> None:
        self.settings.autohotkey_path = self.ahk_path.text().strip() or "AutoHotkey64.exe"
        self.settings.sage_executable_path = self.sage_path.text().strip()
        self.settings.microstore_api_token = self.microstore_token.text().strip()
        self.settings.microstore_sync_days = self.microstore_days.value()
        self.settings.microstore_product_resync_hours = self.microstore_product_resync_hours.value()
        self.settings.portal_order_limit = self.portal_order_limit.value()
        self.settings.efashion_email = self.efashion_email.text().strip()
        self.settings.efashion_password = self.efashion_password.text()
        self.settings.pfs_email = self.pfs_email.text().strip()
        self.settings.pfs_password = self.pfs_password.text()
        if hasattr(self, "product_folder_input"):
            self.settings.product_folder_path = self.product_folder_input.text().strip()
        if hasattr(self, "order_folder_input"):
            self.settings.order_folder_path = self.order_folder_input.text().strip()
        self.settings.portal_email = self.settings.efashion_email or self.settings.pfs_email
        self.settings.sage_profile.injection_mode = REAL_SAGE_ONE_LINE_MODE
        self.settings.sage_profile.window_title_contains = self.window_title.text().strip() or SAGE_50_WINDOW_TITLE
        self.settings.sage_profile.delay_ms = self.delay_ms.value()
        self.settings.sage_profile.stable_pause_ms = self.settings.sage_profile.delay_ms
        if hasattr(self, "confirmation_mode"):
            self.settings.sage_profile.confirmation_mode = str(self.confirmation_mode.currentData() or "simple")
        self.settings.sage_profile.capture_before_after = self.auto_capture.isChecked()
        self.settings.sage_profile.log_enabled = self.injection_logs.isChecked()
        self.settings.injection_line_limit = 0
        self.settings.sage_profile.step_mode = False
        self.settings.auto_close_with_sage = self.auto_close.isChecked()
        self.settings.always_on_top = self.always_on_top.isChecked()
        save_settings(self.settings)

    def _run_sage_diagnostics(self) -> None:
        self._save_app_settings_silent()
        if not is_windows():
            QMessageBox.information(self, APP_NAME, "Diagnostic Sage disponible sur Windows avec AutoHotkey.")
            return
        try:
            launch_ahk_tool(self.settings, "sage_diagnostics.ahk")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Impossible de lancer le diagnostic: {exc}")

    def _watch_sage_process(self) -> None:
        if not is_windows() or not self.settings.auto_close_with_sage:
            return
        sage_path = self.settings.sage_executable_path.strip()
        if not sage_path:
            return
        process_name = Path(sage_path).name
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except Exception:
            return
        if process_name.lower() not in result.stdout.lower():
            self.close()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
