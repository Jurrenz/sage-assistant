from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
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
from .models import InvoiceLine, Product, SageMapping
from .microstore_product_writer import MicrostoreProductWriter, MicrostoreWriteNotEnabled
from .order_folder import OrderFile, list_order_files
from .portal_orders import EfashionConnector, MicrostoreConnector, PfsConnector, PortalOrder, PortalOrderSummary
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

ORDER_SOURCES = ("Toutes", "Microstore", "Fichier manuel", "PFS", "eFashion")
ORDER_STATUSES = ("Tous", STATUS_READY, STATUS_REVIEW, STATUS_INJECTED, STATUS_DONE, STATUS_ERROR)
DATE_FILTERS = ("Toutes", "Aujourd'hui", "7 jours", "30 jours")
ROLE_KIND = Qt.UserRole
ROLE_SOURCE = Qt.UserRole + 1
ROLE_KEY = Qt.UserRole + 2
ROLE_PAYLOAD = Qt.UserRole + 3
ROLE_PRODUCT_REF = Qt.UserRole + 4

COMMAND_HEADERS = ["Source", "N commande", "Client", "Date", "Lignes", "Total", "Statut"]
PRODUCT_HEADERS = ["Référence", "Nom", "Catégorie", "Statut", "Prix", "Colisage", "Dernière activité"]
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
    "Paquets",
    "Colisage",
    "Pieces",
    "Prix commande",
    "Statut",
]
MAPPING_HEADERS = ["Categorie fournisseur", "Code Sage", "Actif"]


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


def _product_activity_iso(product: Product) -> str:
    return max(
        product.last_microstore_modified_at or "",
        product.last_local_modified_at or "",
        product.last_seen_at or "",
        product.last_imported_at or "",
    )


def parse_quick_ref_text(text: str) -> tuple[str, int]:
    cleaned = text.strip().upper().replace("×", "X")
    if not cleaned:
        return "", 1
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


def line_headers_for_source(source: str = "") -> list[str]:
    headers = list(LINE_HEADERS)
    if source == "eFashion":
        headers[7] = "Prix eFashion"
    elif source == "PFS":
        headers[7] = "Prix PFS"
    elif source == "Microstore":
        headers[7] = "Prix Microstore"
    return headers


def populate_lines_table(table: QTableWidget, lines: list[InvoiceLine], editable: bool = False) -> None:
    table.blockSignals(True)
    table.setRowCount(len(lines))
    for row, line in enumerate(lines):
        status = line.validation_status if line.validation_status == "ok" else line.validation_message
        values = [
            line.ref,
            line.type_label,
            line.sage_code,
            line.description,
            str(line.package_count or ""),
            str(line.package_size or ""),
            str(line.quantity_pieces),
            str(line.order_unit_price_ht or line.unit_price_ht or ""),
            status,
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if not editable or col not in {2, 3, 6, 7}:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if col == 7:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            table.setItem(row, col, item)
    table.blockSignals(False)
    table.resizeRowsToContents()


class OrderDetailDialog(QDialog):
    def __init__(
        self,
        lines: list[InvoiceLine],
        summary: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.lines = list(lines)
        self.inject_requested = False
        self.source = summary.get("source", "")
        self.web_url = summary.get("web_url", "")
        self.setWindowTitle(f"Détail commande {summary.get('number', '')}".strip())
        self.resize(1180, 620)

        layout = QVBoxLayout(self)
        summary_box = QGroupBox("Commande")
        summary_layout = QFormLayout(summary_box)
        summary_layout.addRow("Source", QLabel(summary.get("source", "")))
        summary_layout.addRow("N commande", QLabel(summary.get("number", "")))
        summary_layout.addRow("Client", QLabel(summary.get("customer", "")))
        summary_layout.addRow("Date", QLabel(summary.get("date", "")))
        summary_layout.addRow("Total", QLabel(summary.get("total", "")))
        self.status_label = QLabel(summary.get("status", ""))
        summary_layout.addRow("Statut", self.status_label)
        layout.addWidget(summary_box)

        self.message_label = QLabel("")
        layout.addWidget(self.message_label)

        self.table = QTableWidget(0, len(LINE_HEADERS))
        self.table.setHorizontalHeaderLabels(line_headers_for_source(self.source))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        remove_button = QPushButton("Supprimer ligne")
        remove_button.clicked.connect(self._remove_selected_lines)
        save_button = QPushButton("Sauver corrections")
        save_button.clicked.connect(self._save_corrections)
        inject_button = QPushButton("Injecter dans Sage")
        inject_button.clicked.connect(self._accept_for_injection)
        open_web_button = QPushButton("Ouvrir sur le site")
        open_web_button.clicked.connect(self._open_web_page)
        open_web_button.setEnabled(bool(self.web_url))
        copy_link_button = QPushButton("Copier lien")
        copy_link_button.clicked.connect(self._copy_web_link)
        copy_link_button.setEnabled(bool(self.web_url))
        close_button = QPushButton("Fermer")
        close_button.clicked.connect(self.accept)
        actions.addWidget(remove_button)
        actions.addWidget(save_button)
        actions.addWidget(open_web_button)
        actions.addWidget(copy_link_button)
        actions.addStretch(1)
        actions.addWidget(inject_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        populate_lines_table(self.table, self.lines, editable=True)
        self._refresh_status()

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
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        for row in rows:
            if row < len(self.lines):
                del self.lines[row]
        populate_lines_table(self.table, self.lines, editable=True)
        self._refresh_status()

    def _save_corrections(self, show_message: bool = True) -> None:
        for line in self.lines:
            line.validate()
        populate_lines_table(self.table, self.lines, editable=True)
        self._refresh_status()
        if show_message:
            self.message_label.setText("Corrections sauvegardees.")

    def _refresh_status(self) -> None:
        status = _status_from_lines(self.lines)
        self.status_label.setText(status)
        blocked = [line for line in self.lines if line.validation_status != "ok"]
        if blocked:
            self.message_label.setText("Lignes a verifier: " + ", ".join(line.ref for line in blocked[:8]))
        elif not self.message_label.text():
            self.message_label.setText("Toutes les lignes sont pretes.")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row >= len(self.lines):
            return
        line = self.lines[row]
        try:
            line.sage_code = self.table.item(row, 2).text().strip().upper()
            line.description = self.table.item(row, 3).text().strip()
            line.quantity_pieces = int(self.table.item(row, 6).text().strip())
            price_text = self.table.item(row, 7).text().strip().replace(",", ".")
            line.unit_price_ht = Decimal(price_text) if price_text else None
            line.order_unit_price_ht = line.unit_price_ht
            line.price_confirmed = True
        except Exception as exc:
            line.validation_status = "blocked"
            line.validation_message = f"valeur invalide: {exc}"
        else:
            line.validate()


class SageMappingsDialog(QDialog):
    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Mappings Sage")
        self.resize(980, 680)

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
        new_button = QPushButton("Nouveau")
        new_button.clicked.connect(self._clear_form)
        save_button = QPushButton("Ajouter / modifier")
        save_button.clicked.connect(self._save_mapping)
        disable_button = QPushButton("Desactiver")
        disable_button.clicked.connect(self._disable_mapping)
        restore_button = QPushButton("Restaurer defauts")
        restore_button.clicked.connect(self._restore_defaults)
        close_button = QPushButton("Fermer")
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
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
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
        self.table.resizeRowsToContents()

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
        save_button = QPushButton("Sauver")
        save_button.clicked.connect(self._save)
        cancel_button = QPushButton("Annuler")
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
        self.cancel_requested = False

    def cancel(self) -> None:
        self.cancel_requested = True

    def run(self) -> None:
        result = {"sources": {}, "errors": {}, "cancelled": False}
        db = Database(default_db_path())
        resolver = Resolver(db)
        try:
            if "Microstore" in self.sources and not self.cancel_requested:
                self._run_microstore(db, resolver, result)
            if "eFashion" in self.sources and not self.cancel_requested:
                self._run_efashion(db, resolver, result)
            if "PFS" in self.sources and not self.cancel_requested:
                self._run_pfs(db, resolver, result)
        finally:
            db.close()
            self.all_finished.emit(result)

    def _run_microstore(self, db: Database, resolver: Resolver, result: dict) -> None:
        source = "Microstore"
        saved_orders = 0
        product_count = 0
        try:
            self._raise_if_cancelled()
            self._emit_progress(source, 1, "connexion API")
            connector = MicrostoreConnector(self.microstore_token)
            self._raise_if_cancelled()
            self._emit_progress(source, 8, "recuperation produits")
            products = connector.list_products()
            product_count = db.upsert_products(products, mark_missing=True)
            self._raise_if_cancelled()
            self._emit_progress(source, 22, f"{product_count} produits sauvegardes")
            summaries = connector.list_orders(days=self.microstore_days)
            total = len(summaries)
            self._emit_progress(source, 30, f"{total} commandes trouvees")
            for index, summary in enumerate(summaries, start=1):
                self._raise_if_cancelled()
                detail = connector.get_order(summary.order_id)
                self._persist_order(db, resolver, summary, detail)
                saved_orders += 1
                self._emit_detail_progress(source, index, total, saved_orders)
            summary_payload = {"orders": saved_orders, "products": product_count, "cancelled": False}
            result["sources"][source] = summary_payload
            self._emit_progress(source, 100, f"{saved_orders} commandes, {product_count} produits")
            self.source_finished.emit(source, summary_payload)
        except InterruptedError:
            summary_payload = {"orders": saved_orders, "products": product_count, "cancelled": True}
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
            self._emit_progress(source, 1, "connexion")
            connector = EfashionConnector()
            connector.login(self.efashion_email, self.efashion_password)
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
            summary_payload = {"orders": saved_orders, "products": 0, "cancelled": False}
            result["sources"][source] = summary_payload
            self._emit_progress(source, 100, f"{saved_orders} commandes sauvegardees")
            self.source_finished.emit(source, summary_payload)
        except InterruptedError:
            summary_payload = {"orders": saved_orders, "products": 0, "cancelled": True}
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
            self._emit_progress(source, 1, "connexion")
            connector = PfsConnector()
            connector.login(self.pfs_email, self.pfs_password)
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
            summary_payload = {"orders": saved_orders, "products": 0, "cancelled": False}
            result["sources"][source] = summary_payload
            self._emit_progress(source, 100, f"{saved_orders} commandes sauvegardees")
            self.source_finished.emit(source, summary_payload)
        except InterruptedError:
            summary_payload = {"orders": saved_orders, "products": 0, "cancelled": True}
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

        self.setWindowTitle(APP_NAME)
        self.resize(1320, 760)
        self._build_ui()
        self._load_cached_orders()
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
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setFixedWidth(170)
        sidebar.setFrameShape(QFrame.StyledPanel)
        sidebar_layout = QVBoxLayout(sidebar)
        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignCenter)
        self.commands_nav = QPushButton("Commandes")
        self.commands_nav.setCheckable(True)
        self.commands_nav.setChecked(True)
        self.products_nav = QPushButton("Produits")
        self.products_nav.setCheckable(True)
        self.settings_nav = QPushButton("Réglages")
        self.settings_nav.setCheckable(True)
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(self.commands_nav)
        sidebar_layout.addWidget(self.products_nav)
        sidebar_layout.addWidget(self.settings_nav)
        sidebar_layout.addStretch(1)

        self.stack = QStackedWidget()
        self.commands_page = self._build_commands_page()
        self.products_page = self._build_products_page()
        self.settings_page = self._build_settings_page()
        self.stack.addWidget(self.commands_page)
        self.stack.addWidget(self.products_page)
        self.stack.addWidget(self.settings_page)
        self.commands_nav.clicked.connect(lambda: self._show_page(0))
        self.products_nav.clicked.connect(lambda: self._show_page(1))
        self.settings_nav.clicked.connect(lambda: self._show_page(2))

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.commands_nav.setChecked(index == 0)
        self.products_nav.setChecked(index == 1)
        self.settings_nav.setChecked(index == 2)

    def _build_commands_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        filters = QHBoxLayout()
        self.order_search = QLineEdit()
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

        quick_box = QGroupBox("Facture rapide")
        quick_layout = QVBoxLayout(quick_box)
        quick_entry = QHBoxLayout()
        self.quick_ref_input = QLineEdit()
        self.quick_ref_input.setPlaceholderText("Référence ou référence x paquets, ex: FL530-1 ou FL530-1 x2")
        self.quick_ref_input.textChanged.connect(self._refresh_quick_suggestions)
        self.quick_ref_input.returnPressed.connect(self._add_quick_invoice_line)
        quick_add = QPushButton("Ajouter")
        quick_add.clicked.connect(self._add_quick_invoice_line)
        quick_entry.addWidget(self.quick_ref_input, 1)
        quick_entry.addWidget(quick_add)
        quick_layout.addLayout(quick_entry)
        self.quick_suggestions = QListWidget()
        self.quick_suggestions.setMaximumHeight(96)
        self.quick_suggestions.itemDoubleClicked.connect(lambda _item: self._add_quick_invoice_line())
        quick_layout.addWidget(self.quick_suggestions)
        self.quick_table = QTableWidget(0, len(LINE_HEADERS))
        self.quick_table.setHorizontalHeaderLabels(line_headers_for_source("manual"))
        self.quick_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.quick_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.quick_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.quick_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.quick_table.itemChanged.connect(self._on_quick_item_changed)
        quick_layout.addWidget(self.quick_table)
        quick_actions = QHBoxLayout()
        quick_remove = QPushButton("Supprimer ligne")
        quick_remove.clicked.connect(self._remove_quick_invoice_lines)
        quick_clear = QPushButton("Vider")
        quick_clear.clicked.connect(self._clear_quick_invoice_lines)
        quick_inject = QPushButton("Injecter dans Sage")
        quick_inject.clicked.connect(self._inject_quick_invoice)
        self.quick_status = QLabel("Ajoute une référence pour préparer une facture rapide.")
        quick_actions.addWidget(self.quick_status, 1)
        quick_actions.addWidget(quick_remove)
        quick_actions.addWidget(quick_clear)
        quick_actions.addWidget(quick_inject)
        quick_layout.addLayout(quick_actions)
        layout.addWidget(quick_box)

        self.order_table = QTableWidget(0, len(COMMAND_HEADERS))
        self.order_table.setHorizontalHeaderLabels(COMMAND_HEADERS)
        self.order_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.order_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.order_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.order_table.verticalHeader().setDefaultSectionSize(28)
        self.order_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.order_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.order_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.order_table.cellDoubleClicked.connect(lambda _row, _col: self._open_selected_order_detail())
        layout.addWidget(self.order_table, 1)

        actions = QHBoxLayout()
        import_order_button = QPushButton("Importer fichier...")
        import_order_button.clicked.connect(self._import_order)
        sync_orders = QPushButton("Synchroniser")
        sync_orders.clicked.connect(lambda: self._start_sync(["Microstore", "eFashion", "PFS"]))
        detail_button = QPushButton("Ouvrir détails")
        detail_button.clicked.connect(self._open_selected_order_detail)
        open_web_button = QPushButton("Ouvrir sur le site")
        open_web_button.clicked.connect(self._open_selected_order_web_page)
        copy_link_button = QPushButton("Copier lien")
        copy_link_button.clicked.connect(self._copy_selected_order_web_link)
        inject_selected = QPushButton("Injecter dans Sage")
        inject_selected.clicked.connect(self._inject_selected_order_from_folder)
        mark_done = QPushButton("Marquer traité")
        mark_done.clicked.connect(self._mark_selected_order_done)
        actions.addWidget(import_order_button)
        actions.addStretch(1)
        actions.addWidget(sync_orders)
        actions.addWidget(detail_button)
        actions.addWidget(open_web_button)
        actions.addWidget(copy_link_button)
        actions.addWidget(inject_selected)
        actions.addWidget(mark_done)
        layout.addLayout(actions)
        return page

    def _build_products_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.product_status_label = QLabel("")
        layout.addWidget(self.product_status_label)

        filters = QHBoxLayout()
        self.product_search = QLineEdit()
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

        actions = QHBoxLayout()
        refresh_button = QPushButton("Recharger affichage")
        refresh_button.clicked.connect(self._refresh_products_page)
        sync_button = QPushButton("Synchroniser Microstore")
        sync_button.clicked.connect(lambda: self._start_sync(["Microstore"]))
        new_button = QPushButton("Nouveau brouillon")
        new_button.clicked.connect(self._new_product_draft)
        edit_button = QPushButton("Modifier")
        edit_button.clicked.connect(self._edit_selected_product)
        simulate_button = QPushButton("Simulation")
        simulate_button.clicked.connect(self._simulate_selected_product)
        apply_button = QPushButton("Appliquer à Microstore")
        apply_button.clicked.connect(self._apply_selected_product_to_microstore)
        actions.addWidget(refresh_button)
        actions.addWidget(sync_button)
        actions.addStretch(1)
        actions.addWidget(new_button)
        actions.addWidget(edit_button)
        actions.addWidget(simulate_button)
        actions.addWidget(apply_button)
        layout.addLayout(actions)
        return page

    def _build_settings_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(page)

        layout.addWidget(self._build_portals_section())
        layout.addWidget(self._build_sage_section())
        layout.addWidget(self._build_mappings_section())
        layout.addWidget(self._build_injection_section())
        layout.addWidget(self._build_database_section())

        save_row = QHBoxLayout()
        self.missing_types = QLabel("")
        self.missing_types.setWordWrap(True)
        self.missing_types.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        save_settings_button = QPushButton("Sauver reglages")
        save_settings_button.clicked.connect(self._save_app_settings)
        save_row.addWidget(self.missing_types, 1)
        save_row.addWidget(save_settings_button)
        layout.addLayout(save_row)
        scroll.setWidget(page)
        return scroll

    def _build_portals_section(self) -> QGroupBox:
        box = QGroupBox("Synchronisation")
        layout = QVBoxLayout(box)

        microstore_row = QHBoxLayout()
        self.microstore_token = QLineEdit(self.settings.microstore_api_token)
        self.microstore_token.setPlaceholderText("Token admin_token Microstore")
        self.microstore_token.setEchoMode(QLineEdit.Password)
        self.microstore_token.setMinimumWidth(0)
        self.microstore_token.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.microstore_days = QSpinBox()
        self.microstore_days.setRange(1, 365)
        self.microstore_days.setSuffix(" jours")
        self.microstore_days.setValue(self.settings.microstore_sync_days)
        self.portal_order_limit = QSpinBox()
        self.portal_order_limit.setRange(1, 1000)
        self.portal_order_limit.setValue(self.settings.portal_order_limit)
        self.portal_order_limit.setSuffix(" commandes")
        microstore_row.addWidget(QLabel("Token Microstore"))
        microstore_row.addWidget(self.microstore_token, 2)
        microstore_row.addWidget(QLabel("Historique Microstore"))
        microstore_row.addWidget(self.microstore_days)
        microstore_row.addWidget(QLabel("Limite eFashion/PFS"))
        microstore_row.addWidget(self.portal_order_limit)
        layout.addLayout(microstore_row)

        efashion_row = QHBoxLayout()
        self.efashion_email = QLineEdit(self.settings.efashion_email or self.settings.portal_email)
        self.efashion_password = QLineEdit(self.settings.efashion_password)
        self.efashion_password.setEchoMode(QLineEdit.Password)
        self.efashion_email.setMinimumWidth(0)
        self.efashion_password.setMinimumWidth(0)
        efashion_row.addWidget(QLabel("Email eFashion"))
        efashion_row.addWidget(self.efashion_email, 1)
        efashion_row.addWidget(QLabel("Mot de passe eFashion"))
        efashion_row.addWidget(self.efashion_password, 1)
        layout.addLayout(efashion_row)

        pfs_row = QHBoxLayout()
        self.pfs_email = QLineEdit(self.settings.pfs_email or self.settings.portal_email)
        self.pfs_password = QLineEdit(self.settings.pfs_password)
        self.pfs_password.setEchoMode(QLineEdit.Password)
        self.pfs_email.setMinimumWidth(0)
        self.pfs_password.setMinimumWidth(0)
        pfs_row.addWidget(QLabel("Email PFS"))
        pfs_row.addWidget(self.pfs_email, 1)
        pfs_row.addWidget(QLabel("Mot de passe PFS"))
        pfs_row.addWidget(self.pfs_password, 1)
        layout.addLayout(pfs_row)

        actions = QHBoxLayout()
        self.microstore_status = QLabel("Microstore: non configure")
        self.efashion_status = QLabel("eFashion: non connecte")
        self.pfs_status = QLabel("PFS: non connecte")
        for status_label in (self.microstore_status, self.efashion_status, self.pfs_status):
            status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            status_label.setWordWrap(False)
        sync_microstore = QPushButton("Synchroniser Microstore")
        sync_efashion = QPushButton("Synchroniser eFashion")
        sync_pfs = QPushButton("Synchroniser PFS")
        sync_button = QPushButton("Synchroniser tout")
        self.cancel_sync_button = QPushButton("Annuler")
        self.cancel_sync_button.setEnabled(False)
        sync_microstore.clicked.connect(lambda: self._start_sync(["Microstore"]))
        sync_efashion.clicked.connect(lambda: self._start_sync(["eFashion"]))
        sync_pfs.clicked.connect(lambda: self._start_sync(["PFS"]))
        sync_button.clicked.connect(lambda: self._start_sync(["Microstore", "eFashion", "PFS"]))
        self.cancel_sync_button.clicked.connect(self._cancel_sync)
        self.sync_buttons = {
            "Microstore": sync_microstore,
            "eFashion": sync_efashion,
            "PFS": sync_pfs,
            "all": sync_button,
        }
        actions.addStretch(1)
        actions.addWidget(sync_microstore)
        actions.addWidget(sync_efashion)
        actions.addWidget(sync_pfs)
        actions.addWidget(sync_button)
        actions.addWidget(self.cancel_sync_button)
        layout.addLayout(actions)

        progress_layout = QGridLayout()
        progress_layout.setColumnStretch(2, 1)
        self.sync_progress_bars = {}
        self.sync_status_labels = {}
        for row_index, source in enumerate(("Microstore", "eFashion", "PFS")):
            source_label = QLabel(source)
            source_label.setFixedWidth(82)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("%p%")
            bar.setFixedWidth(220)
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
        browse_product_folder = QPushButton("Parcourir")
        browse_product_folder.clicked.connect(self._choose_product_folder)
        product_folder_row.addWidget(QLabel("Import produits Microstore"))
        product_folder_row.addWidget(self.product_folder_input, 1)
        product_folder_row.addWidget(browse_product_folder)
        layout.addLayout(product_folder_row)

        product_actions = QHBoxLayout()
        self.product_export_status = QLabel("Aucune BDD Microstore detectee.")
        refresh_products = QPushButton("Detecter BDD")
        refresh_products.clicked.connect(self._refresh_product_folder)
        import_detected_products = QPushButton("Mettre a jour BDD")
        import_detected_products.clicked.connect(self._import_detected_product_export)
        import_products_button = QPushButton("Importer BDD articles...")
        import_products_button.clicked.connect(self._import_products)
        product_actions.addWidget(self.product_export_status, 1)
        product_actions.addWidget(refresh_products)
        product_actions.addWidget(import_detected_products)
        product_actions.addWidget(import_products_button)
        layout.addLayout(product_actions)

        folder_row = QHBoxLayout()
        self.order_folder_input = QLineEdit(self.settings.order_folder_path)
        self.order_folder_input.setPlaceholderText("Dossier commandes Microstore...")
        browse_order_folder = QPushButton("Parcourir")
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
        layout = QHBoxLayout(box)
        mappings_button = QPushButton("Mappings Sage")
        mappings_button.clicked.connect(self._open_mappings_dialog)
        layout.addWidget(QLabel("Configurer les associations categorie fournisseur -> code Sage."))
        layout.addStretch(1)
        layout.addWidget(mappings_button)
        return box

    def _build_injection_section(self) -> QGroupBox:
        box = QGroupBox("Injection")
        layout = QGridLayout(box)
        self.injection_mode_label = QLabel(REAL_SAGE_INJECTION_LABEL)
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(0, 2000)
        self.delay_ms.setSpecialValueText("0 instant")
        self.delay_ms.setSuffix(" ms")
        self.delay_ms.setValue(self.settings.sage_profile.delay_ms)
        self.confirmation_mode = QComboBox()
        self.confirmation_mode.addItem("Direct", "direct")
        self.confirmation_mode.addItem("Simple", "simple")
        self.confirmation_mode.addItem("Debug", "debug")
        self.confirmation_mode.setCurrentIndex(max(0, self.confirmation_mode.findData(self.settings.sage_profile.confirmation_mode or "simple")))
        self.stable_pause_ms = QSpinBox()
        self.stable_pause_ms.setRange(0, 2000)
        self.stable_pause_ms.setSuffix(" ms")
        self.stable_pause_ms.setValue(self.settings.sage_profile.stable_pause_ms)
        self.auto_capture = QCheckBox("Captures automatiques")
        self.auto_capture.setChecked(self.settings.sage_profile.capture_before_after)
        self.injection_logs = QCheckBox("Logs")
        self.injection_logs.setChecked(self.settings.sage_profile.log_enabled)
        diagnostic_button = QPushButton("Diagnostic Sage")
        diagnostic_button.clicked.connect(self._run_sage_diagnostics)
        layout.addWidget(QLabel("Mode"), 0, 0)
        layout.addWidget(self.injection_mode_label, 0, 1)
        layout.addWidget(QLabel("Confirmation"), 0, 2)
        layout.addWidget(self.confirmation_mode, 0, 3)
        layout.addWidget(QLabel("Délai touches"), 1, 0)
        layout.addWidget(self.delay_ms, 1, 1)
        layout.addWidget(QLabel("Pause stable"), 1, 2)
        layout.addWidget(self.stable_pause_ms, 1, 3)
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
        backup_button = QPushButton("Sauvegarder la base")
        backup_button.setEnabled(False)
        maintenance_button = QPushButton("Maintenance")
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
            f"eFashion : {self.db.count_cached_orders('eFashion')} commandes",
            f"PFS : {self.db.count_cached_orders('PFS')} commandes",
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

    def _selected_product(self) -> Product | None:
        if not hasattr(self, "product_table"):
            return None
        indexes = self.product_table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_index = self.product_proxy.mapToSource(indexes[0])
        ref = self.product_model.data(self.product_model.index(source_index.row(), 0), Qt.UserRole)
        return self.db.get_product_by_ref(str(ref or ""))

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
            populate_lines_table(self.quick_table, self.lines, editable=True)

    def _on_quick_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row >= len(self.lines):
            return
        line = self.lines[row]
        try:
            line.sage_code = self.quick_table.item(row, 2).text().strip().upper()
            line.description = self.quick_table.item(row, 3).text().strip()
            package_text = self.quick_table.item(row, 4).text().strip()
            line.package_count = int(package_text) if package_text else None
            package_size_text = self.quick_table.item(row, 5).text().strip()
            line.package_size = int(package_size_text) if package_size_text else None
            line.quantity_pieces = int(self.quick_table.item(row, 6).text().strip())
            price_text = self.quick_table.item(row, 7).text().strip().replace(",", ".")
            line.unit_price_ht = Decimal(price_text) if price_text else None
            line.order_unit_price_ht = line.unit_price_ht
            line.price_confirmed = True
        except Exception as exc:
            line.validation_status = "blocked"
            line.validation_message = f"valeur invalide: {exc}"
        else:
            line.validate()
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
        writer = MicrostoreProductWriter()
        payload = writer.build_payload(product)
        text = (
            "Simulation Microstore uniquement.\n\n"
            + "\n".join(changes)
            + "\n\nEndpoint prévu: "
            + payload.endpoint
            + "\n\nPayload prévu:\n"
            + json.dumps(payload.payload, ensure_ascii=False, indent=2)
            + "\n\nAucune écriture réelle ne sera lancée tant que le payload goods/add/update n'est pas validé sur une référence test."
        )
        QMessageBox.information(self, APP_NAME, text)
        try:
            writer.apply(product)
        except MicrostoreWriteNotEnabled as exc:
            self.db.log("microstore_write_preview", f"{product.ref}: {exc}")

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
        self.db.log("portal_login", "Connexion PFS OK")
        return True

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
                self.portal_status_cache[cache_key] = _status_from_lines(self._lines_from_portal_order(detail))
        self._apply_order_filters()

    def _start_sync(self, sources: list[str]) -> None:
        if self.sync_threads:
            if hasattr(self, "sync_summary"):
                self.sync_summary.setText("Synchronisation deja en cours.")
            return
        self._save_app_settings_silent()
        runnable_sources: list[str] = []
        if "Microstore" in sources:
            if self.microstore_token.text().strip():
                runnable_sources.append("Microstore")
                self._on_sync_progress("Microstore", 0, "en attente")
            else:
                self.microstore_status.setText("Microstore: token absent")
        if "eFashion" in sources:
            if self._portal_credentials("eFashion"):
                runnable_sources.append("eFashion")
                self._on_sync_progress("eFashion", 0, "en attente")
            else:
                self.efashion_status.setText("eFashion: identifiants absents")
        if "PFS" in sources:
            if self._portal_credentials("PFS"):
                runnable_sources.append("PFS")
                self._on_sync_progress("PFS", 0, "en attente")
            else:
                self.pfs_status.setText("PFS: identifiants absents")
        if not runnable_sources:
            if hasattr(self, "sync_summary"):
                self.sync_summary.setText("Aucune source configuree a synchroniser.")
            return

        self.sync_active_sources = set(runnable_sources)
        for source in runnable_sources:
            button = self.sync_buttons.get(source)
            if button:
                button.setEnabled(False)
        if set(sources) == {"Microstore", "eFashion", "PFS"} and "all" in self.sync_buttons:
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
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_sync_progress)
        worker.source_finished.connect(self._on_sync_source_finished)
        worker.source_error.connect(self._on_sync_source_error)
        worker.all_finished.connect(self._on_sync_all_finished)
        worker.all_finished.connect(thread.quit)
        worker.all_finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda t=thread: self._cleanup_sync_thread(t))
        self.sync_threads.append(thread)
        self.sync_workers.append(worker)
        thread.start()

    def _on_sync_progress(self, source: str, percent: int, message: str) -> None:
        label = f"{source}: {percent}% - {message}"
        if source == "Microstore":
            self.microstore_status.setText(label)
        elif source == "eFashion":
            self.efashion_status.setText(label)
        elif source == "PFS":
            self.pfs_status.setText(label)
        bar = self.sync_progress_bars.get(source)
        if bar:
            bar.setValue(percent)

    def _on_sync_source_finished(self, source: str, summary: dict) -> None:
        orders = int(summary.get("orders") or 0)
        products = int(summary.get("products") or 0)
        cancelled = bool(summary.get("cancelled"))
        if cancelled:
            message = f"annule - {orders} commandes sauvegardees"
        elif source == "Microstore":
            message = f"{orders} commandes, {products} produits"
        else:
            message = f"{orders} commandes sauvegardees"
        self._on_sync_progress(source, 100, message)

    def _on_sync_source_error(self, source: str, message: str) -> None:
        self._on_sync_progress(source, 100, f"erreur - {message}")

    def _on_sync_all_finished(self, result: dict) -> None:
        for source in self.sync_active_sources:
            button = self.sync_buttons.get(source)
            if button:
                button.setEnabled(True)
        if "all" in self.sync_buttons:
            self.sync_buttons["all"].setEnabled(True)
        if hasattr(self, "cancel_sync_button"):
            self.cancel_sync_button.setEnabled(False)
        self._load_cached_orders()
        self._refresh_status()
        self._refresh_missing_types()
        self._refresh_products_page()
        if hasattr(self, "sync_summary"):
            parts = []
            for source, payload in result.get("sources", {}).items():
                orders = int(payload.get("orders") or 0)
                if payload.get("cancelled"):
                    parts.append(f"{source}: annule ({orders} sauvegardees)")
                else:
                    parts.append(f"{source}: {orders} commandes")
            for source, message in result.get("errors", {}).items():
                parts.append(f"{source}: erreur")
            self.sync_summary.setText(" | ".join(parts) if parts else "Synchronisation terminee.")
        self.sync_active_sources.clear()
        self.sync_workers.clear()

    def _cleanup_sync_thread(self, thread: QThread) -> None:
        self.sync_threads = [item for item in self.sync_threads if item is not thread]

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
                self.portal_status_cache[cache_key] = _status_from_lines(self._lines_from_portal_order(detail))
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
        return self.resolver.lines_from_portal_lines(order.lines, source=order.source)

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
                launch_autohotkey(self.settings, path)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Lancement AutoHotkey impossible:\n\n{exc}\n\nFile temporaire creee:\n{path}")
                return False
        if self.current_order_source and self.current_order_key:
            self.db.set_order_status(self.current_order_source, self.current_order_key, STATUS_INJECTED)
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
                self._open_portal_order_detail(order, lines)
                return
            path = self._selected_order_path()
            if not path:
                raise ValueError("Commande fichier introuvable.")
            lines = self._load_order_lines(path)
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
        }
        dialog = OrderDetailDialog(lines, summary, self)
        dialog.exec()
        self.lines = dialog.lines
        self.current_order_path = None
        self.current_order_source = order.source
        self.current_order_key = key
        self.db.set_order_status(order.source, key, _status_from_lines(self.lines))
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
        dialog = OrderDetailDialog(lines, summary, self)
        dialog.exec()
        self.lines = dialog.lines
        self.current_order_path = path
        self.current_order_source = source
        self.current_order_key = key
        self.db.set_order_status(source, key, _status_from_lines(self.lines))
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
                self.current_order_path = None
            else:
                path = self._selected_order_path()
                if not path:
                    raise ValueError("Commande fichier introuvable.")
                self.lines = self._load_order_lines(path)
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
                values = [
                    source,
                    key,
                    self._customer_label(order_file),
                    _display_date(order_file.order_date) or order_file.modified_at_label,
                    str(order_file.line_count) if not order_file.error else "Erreur",
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
                line_count = len(detail.lines) if detail else ""
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
        if hasattr(self, "confirmation_mode"):
            self.settings.sage_profile.confirmation_mode = str(self.confirmation_mode.currentData() or "simple")
        if hasattr(self, "stable_pause_ms"):
            self.settings.sage_profile.stable_pause_ms = self.stable_pause_ms.value()
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
