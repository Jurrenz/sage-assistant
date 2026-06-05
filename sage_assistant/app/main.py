from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import Database
from .excel_import import import_order, import_products
from .injection import launch_ahk_tool, launch_autohotkey, write_injection_queue
from .models import InvoiceLine, SageMapping
from .order_folder import list_order_files
from .product_folder import latest_product_export
from .resolver import Resolver
from .settings import (
    APP_NAME,
    REAL_SAGE_INJECTION_LABEL,
    REAL_SAGE_ONE_LINE_MODE,
    SAGE_50_WINDOW_TITLE,
    is_windows,
    load_settings,
    save_settings,
)


LINE_HEADERS = [
    "Reference",
    "Categorie",
    "Code Sage",
    "Paquets",
    "Colisage",
    "Qte pieces",
    "Prix BDD",
    "Prix commande",
    "Prix retenu",
    "Statut",
]


def populate_lines_table(table: QTableWidget, lines: list[InvoiceLine], editable: bool = False) -> None:
    table.blockSignals(True)
    table.setRowCount(len(lines))
    for row, line in enumerate(lines):
        values = [
            line.ref,
            line.type_label,
            line.sage_code,
            str(line.package_count or ""),
            str(line.package_size or ""),
            str(line.quantity_pieces),
            str(line.catalog_unit_price_ht or ""),
            str(line.order_unit_price_ht or ""),
            str(line.unit_price_ht or ""),
            line.validation_status if line.validation_status == "ok" else line.validation_message,
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if not editable or col in {0, 1, 3, 4, 6, 7, 9}:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, col, item)
    table.blockSignals(False)


class OrderDetailDialog(QDialog):
    def __init__(self, lines: list[InvoiceLine], title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lines = list(lines)
        self.inject_requested = False
        self.setWindowTitle(title)
        self.resize(1050, 520)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(LINE_HEADERS))
        self.table.setHorizontalHeaderLabels(LINE_HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        remove_button = QPushButton("Supprimer ligne")
        remove_button.clicked.connect(self._remove_selected_lines)
        inject_button = QPushButton("Injecter dans Sage")
        inject_button.clicked.connect(self._accept_for_injection)
        close_button = QPushButton("Fermer")
        close_button.clicked.connect(self.accept)
        actions.addWidget(remove_button)
        actions.addStretch(1)
        actions.addWidget(inject_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        populate_lines_table(self.table, self.lines, editable=True)

    def _accept_for_injection(self) -> None:
        self.inject_requested = True
        self.accept()

    def _remove_selected_lines(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        for row in rows:
            if row < len(self.lines):
                del self.lines[row]
        populate_lines_table(self.table, self.lines, editable=True)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row >= len(self.lines):
            return
        line = self.lines[row]
        try:
            line.sage_code = self.table.item(row, 2).text().strip().upper()
            line.quantity_pieces = int(self.table.item(row, 5).text().strip())
            price_text = self.table.item(row, 8).text().strip().replace(",", ".")
            line.unit_price_ht = Decimal(price_text) if price_text else None
            line.price_confirmed = True
        except Exception as exc:
            line.validation_status = "blocked"
            line.validation_message = f"valeur invalide: {exc}"
        else:
            line.validate()
        populate_lines_table(self.table, self.lines, editable=True)


class MappingDialog(QDialog):
    def __init__(self, db: Database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Mappings Sage")
        self.resize(760, 520)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mapping_type = QLineEdit()
        self.mapping_code = QLineEdit()
        self.mapping_label = QLineEdit()
        form.addRow("Type Microstore", self.mapping_type)
        form.addRow("Code Sage", self.mapping_code)
        form.addRow("Libelle Sage", self.mapping_label)
        layout.addLayout(form)

        actions = QHBoxLayout()
        add_button = QPushButton("Nouveau")
        add_button.clicked.connect(self._clear_form)
        save_button = QPushButton("Sauver")
        save_button.clicked.connect(self._save_mapping)
        delete_button = QPushButton("Supprimer")
        delete_button.clicked.connect(self._delete_mapping)
        restore_button = QPushButton("Restaurer defauts")
        restore_button.clicked.connect(self._restore_defaults)
        actions.addWidget(add_button)
        actions.addWidget(save_button)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        actions.addWidget(restore_button)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Type Microstore", "Code Sage", "Libelle Sage"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._load_selected_mapping)
        layout.addWidget(self.table, 1)

        close_button = QPushButton("Fermer")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        self.refresh()

    def refresh(self) -> None:
        mappings = self.db.list_mappings(active_only=True)
        self.table.setRowCount(len(mappings))
        for row, mapping in enumerate(mappings):
            self.table.setItem(row, 0, QTableWidgetItem(mapping.microstore_type))
            self.table.setItem(row, 1, QTableWidgetItem(mapping.sage_code))
            self.table.setItem(row, 2, QTableWidgetItem(mapping.sage_label))

    def _clear_form(self) -> None:
        self.table.clearSelection()
        self.mapping_type.clear()
        self.mapping_code.clear()
        self.mapping_label.clear()

    def _save_mapping(self) -> None:
        mapping = SageMapping(
            microstore_type=self.mapping_type.text().strip(),
            sage_code=self.mapping_code.text().strip(),
            sage_label=self.mapping_label.text().strip(),
        )
        if not mapping.microstore_type or not mapping.sage_code or not mapping.sage_label:
            QMessageBox.warning(self, APP_NAME, "Type, code Sage et libelle sont obligatoires.")
            return
        self.db.upsert_mapping(mapping)
        self.refresh()

    def _delete_mapping(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        if not rows:
            QMessageBox.information(self, APP_NAME, "Selectionne un mapping a supprimer.")
            return
        microstore_type = self.table.item(rows[0], 0).text()
        self.db.deactivate_mapping(microstore_type)
        self._clear_form()
        self.refresh()

    def _restore_defaults(self) -> None:
        inserted = self.db.seed_default_mappings()
        self.refresh()
        QMessageBox.information(self, APP_NAME, f"{inserted} mapping(s) par defaut restaure(s).")

    def _load_selected_mapping(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        if not rows:
            return
        row = rows[0]
        self.mapping_type.setText(self.table.item(row, 0).text())
        self.mapping_code.setText(self.table.item(row, 1).text())
        self.mapping_label.setText(self.table.item(row, 2).text())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.resolver = Resolver(self.db)
        self.settings = load_settings()
        self.lines: list[InvoiceLine] = []
        self.current_order_path: Path | None = None
        self.detected_order_files = []
        self.detected_product_export = None

        self.setWindowTitle(APP_NAME)
        self.resize(1220, 720)
        self._build_menu()
        self._build_ui()
        self._apply_window_flags()
        self._refresh_status()
        self._refresh_missing_types()
        self._refresh_product_folder()
        self._refresh_order_folder()

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

    def _build_menu(self) -> None:
        toolbar = self.addToolBar("Actions")
        pin_action = QAction("Epingler", self)
        pin_action.setCheckable(True)
        pin_action.setChecked(self.settings.always_on_top)
        pin_action.triggered.connect(self._toggle_always_on_top)
        toolbar.addAction(pin_action)
        toolbar.addSeparator()
        mappings_action = QAction("Mappings Sage", self)
        mappings_action.triggered.connect(self._open_mapping_dialog)
        toolbar.addAction(mappings_action)
        diagnostic_action = QAction("Diagnostic Sage", self)
        diagnostic_action.triggered.connect(self._run_sage_diagnostics)
        toolbar.addAction(diagnostic_action)

    def _build_ui(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._build_command_panel(), 2)
        layout.addWidget(self._build_settings_panel(), 1)
        self.setCentralWidget(page)

    def _build_command_panel(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.order_table = QTableWidget(0, 11)
        self.order_table.setHorizontalHeaderLabels(
            [
                "Commande",
                "Client",
                "Ville",
                "Date commande",
                "Acceptation/fichier",
                "Lignes",
                "Paquets",
                "Pieces",
                "Total",
                "Tel",
                "Email",
            ]
        )
        self.order_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.order_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.order_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeToContents)
        self.order_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.order_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.order_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.order_table.cellDoubleClicked.connect(lambda _row, _col: self._open_selected_order_detail())
        layout.addWidget(self.order_table, 1)

        detected_row = QHBoxLayout()
        import_order_button = QPushButton("Importer fichier...")
        import_order_button.clicked.connect(self._import_order)
        refresh_orders = QPushButton("Rafraichir")
        refresh_orders.clicked.connect(self._refresh_order_folder)
        detected_row.addWidget(import_order_button)
        detail_button = QPushButton("Detail commande")
        detail_button.clicked.connect(self._open_selected_order_detail)
        detected_row.addStretch(1)
        detected_row.addWidget(refresh_orders)
        detected_row.addWidget(detail_button)
        inject_selected = QPushButton("Injecter dans Sage")
        inject_selected.clicked.connect(self._inject_selected_order_from_folder)
        detected_row.addWidget(inject_selected)
        layout.addLayout(detected_row)
        return page

    def _build_settings_panel(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.missing_types = QLabel("")
        layout.addWidget(self.missing_types)

        product_folder_row = QHBoxLayout()
        self.product_folder_input = QLineEdit(self.settings.product_folder_path)
        self.product_folder_input.setPlaceholderText("Dossier MS_IMPORT Google Drive...")
        browse_product_folder = QPushButton("Parcourir")
        browse_product_folder.clicked.connect(self._choose_product_folder)
        product_folder_row.addWidget(QLabel("Dossier BDD Microstore"))
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
        folder_row.addWidget(QLabel("Dossier commandes"))
        folder_row.addWidget(self.order_folder_input, 1)
        folder_row.addWidget(browse_order_folder)
        layout.addLayout(folder_row)

        settings_form = QFormLayout()
        self.ahk_path = QLineEdit(self.settings.autohotkey_path)
        self.sage_path = QLineEdit(self.settings.sage_executable_path)
        self.window_title = QLineEdit(self.settings.sage_profile.window_title_contains)
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(10, 2000)
        self.delay_ms.setValue(self.settings.sage_profile.delay_ms)
        self.auto_close = QCheckBox("Fermer l'assistant quand Sage se ferme")
        self.auto_close.setChecked(self.settings.auto_close_with_sage)
        self.injection_mode_label = QLabel(REAL_SAGE_INJECTION_LABEL)
        settings_form.addRow("AutoHotkey.exe", self.ahk_path)
        settings_form.addRow("Sage.exe", self.sage_path)
        settings_form.addRow("Mode injection", self.injection_mode_label)
        settings_form.addRow("Titre fenetre Sage contient", self.window_title)
        settings_form.addRow("Delai touches (ms)", self.delay_ms)
        settings_form.addRow("", self.auto_close)
        layout.addLayout(settings_form)

        settings_actions = QHBoxLayout()
        mappings_button = QPushButton("Mappings Sage")
        mappings_button.clicked.connect(self._open_mapping_dialog)
        diagnostic_button = QPushButton("Diagnostic Sage")
        diagnostic_button.clicked.connect(self._run_sage_diagnostics)
        save_settings_button = QPushButton("Sauver reglages")
        save_settings_button.clicked.connect(self._save_app_settings)
        settings_actions.addWidget(mappings_button)
        settings_actions.addWidget(diagnostic_button)
        settings_actions.addStretch(1)
        settings_actions.addWidget(save_settings_button)
        layout.addLayout(settings_actions)
        return page

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
        self.status_label.setText(f"Dernier import produits: {latest or 'aucun'}")

    def _refresh_missing_types(self) -> None:
        missing = self.db.list_types_without_mapping()
        if missing:
            self.missing_types.setText("Types sans mapping: " + ", ".join(missing[:20]))
        else:
            self.missing_types.setText("Tous les types produits connus ont un mapping.")

    def _prepare_injection(self) -> None:
        if not self.lines:
            QMessageBox.information(self, APP_NAME, "Aucune ligne a injecter.")
            return
        self._save_app_settings_silent()
        for line in self.lines:
            line.validate()
        blocked = [line for line in self.lines if line.validation_status != "ok"]
        if blocked:
            refs = ", ".join(line.ref for line in blocked[:8])
            QMessageBox.warning(self, APP_NAME, f"Certaines lignes sont bloquees. Corrige les mappings ou supprime-les en detail commande.\n\n{refs}")
            return
        try:
            path = write_injection_queue(
                self.lines,
                self.settings,
            )
            self.db.log("injection_prepare", f"{len(self.lines)}/{len(self.lines)} lignes preparees: {path}")
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        if is_windows():
            try:
                launch_autohotkey(self.settings, path)
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"File creee, mais lancement AHK impossible: {exc}")
                return
        QMessageBox.information(self, APP_NAME, f"File injection prete:\n{path}")

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
        QMessageBox.information(self, APP_NAME, f"{count} references importees.\n" + "\n".join(result.warnings[:10]))

    def _import_order(self) -> None:
        path = self._pick_excel_file("Choisir commande Microstore")
        if not path:
            return
        self._import_order_path(path)

    def _load_order_lines(self, path: Path) -> list[InvoiceLine]:
        result = import_order(path)
        lines = [self.resolver.line_from_order_row(row) for row in result.rows]  # type: ignore[arg-type]
        self.db.log("order_import", f"{len(lines)} lignes commande importees depuis {path.name}")
        return lines

    def _import_order_path(self, path: Path) -> None:
        try:
            self.lines = self._load_order_lines(path)
            self.current_order_path = path
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._open_order_detail(path, self.lines)

    def _selected_order_path(self) -> Path | None:
        rows = sorted({item.row() for item in self.order_table.selectedItems()})
        if not rows:
            return None
        path_value = self.order_table.item(rows[0], 0).data(Qt.UserRole)
        return Path(path_value)

    def _open_selected_order_detail(self) -> None:
        path = self._selected_order_path()
        if not path:
            QMessageBox.information(self, APP_NAME, "Aucune commande selectionnee.")
            return
        try:
            lines = self._load_order_lines(path)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._open_order_detail(path, lines)

    def _open_order_detail(self, path: Path, lines: list[InvoiceLine]) -> None:
        dialog = OrderDetailDialog(lines, f"Detail commande {path.stem}", self)
        dialog.exec()
        self.lines = dialog.lines
        self.current_order_path = path
        if dialog.inject_requested:
            self._prepare_injection()

    def _inject_selected_order_from_folder(self) -> None:
        path = self._selected_order_path()
        if not path:
            QMessageBox.information(self, APP_NAME, "Aucune commande selectionnee.")
            return
        if self.current_order_path != path or not self.lines:
            try:
                self.lines = self._load_order_lines(path)
                self.current_order_path = path
            except Exception as exc:
                QMessageBox.critical(self, APP_NAME, str(exc))
                return
        self._prepare_injection()

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
        self.detected_order_files = list_order_files(folder) if folder else []
        self.order_table.setRowCount(len(self.detected_order_files[:100]))
        for row, order_file in enumerate(self.detected_order_files[:100]):
            customer = order_file.customer_name or order_file.customer_company
            values = [
                order_file.order_number or order_file.path.stem,
                customer,
                " ".join(part for part in (order_file.customer_zip, order_file.customer_city) if part),
                order_file.order_date,
                order_file.modified_at_label,
                str(order_file.line_count) if not order_file.error else "Erreur",
                str(order_file.package_count) if not order_file.error else "",
                str(order_file.piece_count) if not order_file.error else "",
                order_file.total_amount_label if not order_file.error else "",
                order_file.customer_phone if not order_file.error else order_file.error,
                order_file.customer_email if not order_file.error else "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, str(order_file.path))
                self.order_table.setItem(row, col, item)
        self.order_table.resizeRowsToContents()

    def _pick_excel_file(self, title: str) -> Path | None:
        filename, _ = QFileDialog.getOpenFileName(self, title, "", "Excel (*.xlsx *.xlsm *.xls)")
        return Path(filename) if filename else None

    def _open_mapping_dialog(self) -> None:
        dialog = MappingDialog(self.db, self)
        dialog.exec()
        self._refresh_missing_types()

    def _save_app_settings(self) -> None:
        self.settings.autohotkey_path = self.ahk_path.text().strip() or "AutoHotkey64.exe"
        self.settings.sage_executable_path = self.sage_path.text().strip()
        self.settings.product_folder_path = self.product_folder_input.text().strip()
        self.settings.order_folder_path = self.order_folder_input.text().strip()
        self.settings.sage_profile.injection_mode = REAL_SAGE_ONE_LINE_MODE
        self.settings.sage_profile.window_title_contains = self.window_title.text().strip() or SAGE_50_WINDOW_TITLE
        self.settings.sage_profile.delay_ms = self.delay_ms.value()
        self.settings.injection_line_limit = 0
        self.settings.sage_profile.step_mode = False
        self.settings.auto_close_with_sage = self.auto_close.isChecked()
        save_settings(self.settings)
        QMessageBox.information(self, APP_NAME, "Reglages sauvegardes.")

    def _save_app_settings_silent(self) -> None:
        self.settings.autohotkey_path = self.ahk_path.text().strip() or "AutoHotkey64.exe"
        self.settings.sage_executable_path = self.sage_path.text().strip()
        self.settings.product_folder_path = self.product_folder_input.text().strip()
        self.settings.order_folder_path = self.order_folder_input.text().strip()
        self.settings.sage_profile.injection_mode = REAL_SAGE_ONE_LINE_MODE
        self.settings.sage_profile.window_title_contains = self.window_title.text().strip() or SAGE_50_WINDOW_TITLE
        self.settings.sage_profile.delay_ms = self.delay_ms.value()
        self.settings.injection_line_limit = 0
        self.settings.sage_profile.step_mode = False
        self.settings.auto_close_with_sage = self.auto_close.isChecked()
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
