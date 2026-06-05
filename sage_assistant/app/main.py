from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import Database
from .excel_import import import_order, import_products
from .injection import launch_ahk_tool, launch_autohotkey, write_injection_queue
from .models import InvoiceLine, SageMapping
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

COMMAND_HEADERS = ["Source", "N commande", "Client", "Date", "Lignes", "Total", "Statut"]
LINE_HEADERS = [
    "Reference",
    "Categorie",
    "Code Sage",
    "Description",
    "Paquets",
    "Colisage",
    "Pieces",
    "Prix retenu",
    "Statut",
]
MAPPING_HEADERS = ["Categorie fournisseur", "Code Sage", "Actif"]


def _money_label(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f} EUR"


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
            str(line.unit_price_ht or ""),
            status,
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if not editable or col not in {2, 6, 7}:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
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
        self.table.setHorizontalHeaderLabels(LINE_HEADERS)
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
        close_button = QPushButton("Fermer")
        close_button.clicked.connect(self.accept)
        actions.addWidget(remove_button)
        actions.addWidget(save_button)
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
            line.quantity_pieces = int(self.table.item(row, 6).text().strip())
            price_text = self.table.item(row, 7).text().strip().replace(",", ".")
            line.unit_price_ht = Decimal(price_text) if price_text else None
            line.price_confirmed = True
        except Exception as exc:
            line.validation_status = "blocked"
            line.validation_message = f"valeur invalide: {exc}"
        else:
            line.validate()


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

        self.setWindowTitle(APP_NAME)
        self.resize(1320, 760)
        self._build_ui()
        self._apply_window_flags()
        self._refresh_status()
        self._refresh_missing_types()
        self._refresh_product_folder()
        self._refresh_order_folder()
        self._refresh_mappings_table()
        if self.settings.microstore_api_token:
            QTimer.singleShot(300, self._sync_microstore_api_silent)

        self.sage_watch_timer = QTimer(self)
        self.sage_watch_timer.timeout.connect(self._watch_sage_process)
        self.sage_watch_timer.start(3000)

        self.order_folder_timer = QTimer(self)
        self.order_folder_timer.timeout.connect(self._refresh_order_folder)
        self.order_folder_timer.start(5000)

        self.product_folder_timer = QTimer(self)
        self.product_folder_timer.timeout.connect(self._refresh_product_folder)
        self.product_folder_timer.start(30000)

    def closeEvent(self, event) -> None:  # type: ignore[override]
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
        self.settings_nav = QPushButton("Réglages")
        self.settings_nav.setCheckable(True)
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(self.commands_nav)
        sidebar_layout.addWidget(self.settings_nav)
        sidebar_layout.addStretch(1)

        self.stack = QStackedWidget()
        self.commands_page = self._build_commands_page()
        self.settings_page = self._build_settings_page()
        self.stack.addWidget(self.commands_page)
        self.stack.addWidget(self.settings_page)
        self.commands_nav.clicked.connect(lambda: self._show_page(0))
        self.settings_nav.clicked.connect(lambda: self._show_page(1))

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

    def _show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.commands_nav.setChecked(index == 0)
        self.settings_nav.setChecked(index == 1)

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

        self.order_table = QTableWidget(0, len(COMMAND_HEADERS))
        self.order_table.setHorizontalHeaderLabels(COMMAND_HEADERS)
        self.order_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.order_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.order_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.order_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.order_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.order_table.cellDoubleClicked.connect(lambda _row, _col: self._open_selected_order_detail())
        layout.addWidget(self.order_table, 1)

        actions = QHBoxLayout()
        import_order_button = QPushButton("Importer fichier...")
        import_order_button.clicked.connect(self._import_order)
        refresh_orders = QPushButton("Rafraichir")
        refresh_orders.clicked.connect(self._refresh_orders)
        sync_portals = QPushButton("Synchroniser portails")
        sync_portals.clicked.connect(self._sync_portal_orders)
        detail_button = QPushButton("Ouvrir détails")
        detail_button.clicked.connect(self._open_selected_order_detail)
        inject_selected = QPushButton("Injecter dans Sage")
        inject_selected.clicked.connect(self._inject_selected_order_from_folder)
        mark_done = QPushButton("Marquer traité")
        mark_done.clicked.connect(self._mark_selected_order_done)
        actions.addWidget(import_order_button)
        actions.addStretch(1)
        actions.addWidget(refresh_orders)
        actions.addWidget(sync_portals)
        actions.addWidget(detail_button)
        actions.addWidget(inject_selected)
        actions.addWidget(mark_done)
        layout.addLayout(actions)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(self._build_folders_section())
        layout.addWidget(self._build_portals_section())
        layout.addWidget(self._build_sage_section())
        layout.addWidget(self._build_mappings_section(), 1)
        layout.addWidget(self._build_injection_section())
        layout.addWidget(self._build_database_section())

        save_row = QHBoxLayout()
        self.missing_types = QLabel("")
        save_settings_button = QPushButton("Sauver reglages")
        save_settings_button.clicked.connect(self._save_app_settings)
        save_row.addWidget(self.missing_types, 1)
        save_row.addWidget(save_settings_button)
        layout.addLayout(save_row)
        return page

    def _build_portals_section(self) -> QGroupBox:
        box = QGroupBox("Portails")
        layout = QVBoxLayout(box)

        credentials = QHBoxLayout()
        self.microstore_token = QLineEdit(self.settings.microstore_api_token)
        self.microstore_token.setPlaceholderText("Token admin_token Microstore")
        self.microstore_token.setEchoMode(QLineEdit.Password)
        self.microstore_days = QSpinBox()
        self.microstore_days.setRange(1, 365)
        self.microstore_days.setSuffix(" jours")
        self.microstore_days.setValue(self.settings.microstore_sync_days)
        self.portal_email = QLineEdit(self.settings.portal_email)
        self.portal_email.setPlaceholderText("Email portail")
        self.portal_password = QLineEdit()
        self.portal_password.setPlaceholderText("Mot de passe non sauvegarde")
        self.portal_password.setEchoMode(QLineEdit.Password)
        credentials.addWidget(QLabel("Token Microstore"))
        credentials.addWidget(self.microstore_token, 2)
        credentials.addWidget(QLabel("Historique"))
        credentials.addWidget(self.microstore_days)
        layout.addLayout(credentials)

        credentials = QHBoxLayout()
        credentials.addWidget(QLabel("Email PFS/eFashion"))
        credentials.addWidget(self.portal_email, 1)
        credentials.addWidget(QLabel("Mot de passe"))
        credentials.addWidget(self.portal_password, 1)
        layout.addLayout(credentials)

        actions = QHBoxLayout()
        self.microstore_status = QLabel("Microstore API: non configure")
        self.efashion_status = QLabel("eFashion: non connecte")
        self.pfs_status = QLabel("PFS: non connecte")
        sync_microstore = QPushButton("Synchroniser Microstore")
        sync_microstore.clicked.connect(self._sync_microstore_api)
        connect_efashion = QPushButton("Connexion eFashion")
        connect_efashion.clicked.connect(self._login_efashion)
        connect_pfs = QPushButton("Connexion PFS")
        connect_pfs.clicked.connect(self._login_pfs)
        sync_portals = QPushButton("Synchroniser commandes")
        sync_portals.clicked.connect(self._sync_portal_orders)
        actions.addWidget(self.microstore_status)
        actions.addWidget(self.efashion_status)
        actions.addWidget(self.pfs_status)
        actions.addStretch(1)
        actions.addWidget(sync_microstore)
        actions.addWidget(connect_efashion)
        actions.addWidget(connect_pfs)
        actions.addWidget(sync_portals)
        layout.addLayout(actions)
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
        layout = QVBoxLayout(box)
        form = QHBoxLayout()
        self.mapping_type = QLineEdit()
        self.mapping_type.setPlaceholderText("Categorie fournisseur")
        self.mapping_code = QLineEdit()
        self.mapping_code.setPlaceholderText("Code Sage")
        form.addWidget(self.mapping_type, 2)
        form.addWidget(self.mapping_code)
        layout.addLayout(form)

        actions = QHBoxLayout()
        new_button = QPushButton("Nouveau")
        new_button.clicked.connect(self._clear_mapping_form)
        save_button = QPushButton("Ajouter / modifier")
        save_button.clicked.connect(self._save_mapping)
        disable_button = QPushButton("Desactiver")
        disable_button.clicked.connect(self._delete_mapping)
        restore_button = QPushButton("Restaurer defauts")
        restore_button.clicked.connect(self._restore_default_mappings)
        actions.addWidget(new_button)
        actions.addWidget(save_button)
        actions.addWidget(disable_button)
        actions.addStretch(1)
        actions.addWidget(restore_button)
        layout.addLayout(actions)

        self.mapping_table = QTableWidget(0, len(MAPPING_HEADERS))
        self.mapping_table.setHorizontalHeaderLabels(MAPPING_HEADERS)
        self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.mapping_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.mapping_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.mapping_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mapping_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.mapping_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mapping_table.itemSelectionChanged.connect(self._load_selected_mapping)
        layout.addWidget(self.mapping_table, 1)
        return box

    def _build_injection_section(self) -> QGroupBox:
        box = QGroupBox("Injection")
        layout = QHBoxLayout(box)
        self.injection_mode_label = QLabel(REAL_SAGE_INJECTION_LABEL)
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(0, 2000)
        self.delay_ms.setSpecialValueText("0 instant")
        self.delay_ms.setSuffix(" ms")
        self.delay_ms.setValue(self.settings.sage_profile.delay_ms)
        self.auto_capture = QCheckBox("Captures automatiques")
        self.auto_capture.setChecked(self.settings.sage_profile.capture_before_after)
        self.injection_logs = QCheckBox("Logs")
        self.injection_logs.setChecked(self.settings.sage_profile.log_enabled)
        diagnostic_button = QPushButton("Diagnostic Sage")
        diagnostic_button.clicked.connect(self._run_sage_diagnostics)
        layout.addWidget(QLabel("Mode"))
        layout.addWidget(self.injection_mode_label)
        layout.addWidget(QLabel("Delai touches (ms)"))
        layout.addWidget(self.delay_ms)
        layout.addWidget(self.auto_capture)
        layout.addWidget(self.injection_logs)
        layout.addStretch(1)
        layout.addWidget(diagnostic_button)
        return box

    def _build_database_section(self) -> QGroupBox:
        box = QGroupBox("Base de donnees")
        layout = QHBoxLayout(box)
        db_path = QLineEdit(str(default_db_path()))
        db_path.setReadOnly(True)
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
        latest = self.db.latest_product_import()
        source = "Microstore API" if self.settings.microstore_api_token else "import produits"
        self.status_label.setText(f"Derniere mise a jour produits ({source}): {_display_date(latest) if latest else 'aucune'}")

    def _refresh_missing_types(self) -> None:
        if not hasattr(self, "missing_types"):
            return
        missing = self.db.list_types_without_mapping()
        if missing:
            self.missing_types.setText("Types sans mapping: " + ", ".join(missing[:20]))
        else:
            self.missing_types.setText("Tous les types produits connus ont un mapping.")

    def _portal_credentials(self) -> tuple[str, str] | None:
        email = self.portal_email.text().strip()
        password = self.portal_password.text()
        if not email or not password:
            QMessageBox.warning(self, APP_NAME, "Email et mot de passe portail sont obligatoires.")
            return None
        self.settings.portal_email = email
        save_settings(self.settings)
        return email, password

    def _login_efashion(self) -> None:
        credentials = self._portal_credentials()
        if not credentials:
            return
        email, password = credentials
        try:
            session = self.efashion_connector.login(email, password)
        except Exception as exc:
            self.efashion_status.setText("eFashion: erreur")
            QMessageBox.critical(self, APP_NAME, f"Connexion eFashion impossible: {exc}")
            return
        self.efashion_status.setText(f"eFashion: connecte ({session.user_label})")
        self.db.log("portal_login", "Connexion eFashion OK")

    def _login_pfs(self) -> None:
        credentials = self._portal_credentials()
        if not credentials:
            return
        email, password = credentials
        try:
            session = self.pfs_connector.login(email, password)
        except Exception as exc:
            self.pfs_status.setText("PFS: erreur")
            QMessageBox.critical(self, APP_NAME, f"Connexion PFS impossible: {exc}")
            return
        self.pfs_status.setText(f"PFS: connecte ({session.user_label})")
        self.db.log("portal_login", "Connexion PFS OK")

    def _sync_microstore_api_silent(self) -> None:
        try:
            self._sync_microstore_api(show_message=False)
        except Exception:
            pass

    def _sync_microstore_api(self, show_message: bool = True) -> None:
        token = self.microstore_token.text().strip()
        if not token:
            if show_message:
                QMessageBox.warning(self, APP_NAME, "Renseigne le token Microstore avant de synchroniser.")
            return
        self.settings.microstore_api_token = token
        self.settings.microstore_sync_days = self.microstore_days.value()
        self.microstore_connector.set_token(token)
        save_settings(self.settings)
        try:
            products = self.microstore_connector.list_products()
            product_count = self.db.upsert_products(products)
            summaries = self.microstore_connector.list_orders(days=self.settings.microstore_sync_days)
            order_count = self._sync_source_orders("Microstore", summaries)
        except Exception as exc:
            self.microstore_status.setText("Microstore API: erreur")
            if show_message:
                QMessageBox.critical(self, APP_NAME, f"Synchronisation Microstore impossible: {exc}")
            return
        self.microstore_status.setText(f"Microstore API: {order_count} commandes, {product_count} produits")
        self._refresh_status()
        self._refresh_missing_types()
        self._apply_order_filters()
        if show_message:
            QMessageBox.information(self, APP_NAME, f"Microstore synchronise: {product_count} produits, {order_count} commandes.")

    def _refresh_orders(self) -> None:
        if self.microstore_token.text().strip():
            self._sync_microstore_api(show_message=True)
        else:
            self._refresh_order_folder()

    def _sync_portal_orders(self) -> None:
        synced = 0
        errors: list[str] = []
        if self.microstore_token.text().strip():
            try:
                self._sync_microstore_api(show_message=False)
                synced += len([1 for source, _key in self.portal_summaries if source == "Microstore"])
            except Exception as exc:
                errors.append(f"Microstore: {exc}")
        if self.efashion_connector.session:
            try:
                synced += self._sync_source_orders("eFashion", self.efashion_connector.list_orders(page=1, limit=25))
            except Exception as exc:
                errors.append(f"eFashion: {exc}")
        if self.pfs_connector.session:
            try:
                synced += self._sync_source_orders("PFS", self.pfs_connector.list_orders(page=1, per_page=25))
            except Exception as exc:
                errors.append(f"PFS: {exc}")
        if not self.microstore_token.text().strip() and not self.efashion_connector.session and not self.pfs_connector.session:
            QMessageBox.information(self, APP_NAME, "Configure Microstore ou connecte au moins un portail avant de synchroniser.")
            return
        self._apply_order_filters()
        message = f"{synced} commande(s) portail synchronisee(s)."
        if errors:
            message += "\n\n" + "\n".join(errors)
        QMessageBox.information(self, APP_NAME, message)

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
            self.db.log("injection_prepare", f"{len(self.lines)}/{len(self.lines)} lignes preparees: {path}")
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return False
        if is_windows():
            try:
                launch_autohotkey(self.settings, path)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"File creee, mais lancement AHK impossible: {exc}")
                return False
        if self.current_order_source and self.current_order_key:
            self.db.set_order_status(self.current_order_source, self.current_order_key, STATUS_INJECTED)
            self._refresh_order_folder()
        QMessageBox.information(self, APP_NAME, f"File injection prete:\n{path}")
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
        folder = self.order_folder_input.text().strip()
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
        self.order_table.resizeRowsToContents()

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
        self.settings.product_folder_path = self.product_folder_input.text().strip()
        self.settings.order_folder_path = self.order_folder_input.text().strip()
        self.settings.portal_email = self.portal_email.text().strip()
        self.settings.sage_profile.injection_mode = REAL_SAGE_ONE_LINE_MODE
        self.settings.sage_profile.window_title_contains = self.window_title.text().strip() or SAGE_50_WINDOW_TITLE
        self.settings.sage_profile.delay_ms = self.delay_ms.value()
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
