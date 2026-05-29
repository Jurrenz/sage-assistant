from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import Database
from .excel_import import import_order, import_products
from .injection import launch_autohotkey, write_injection_queue
from .models import InvoiceLine, SageMapping
from .resolver import Resolver
from .settings import APP_NAME, AppSettings, is_windows, load_settings, save_settings


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.resolver = Resolver(self.db)
        self.settings = load_settings()
        self.lines: list[InvoiceLine] = []
        self.current_matches = []

        self.setWindowTitle(APP_NAME)
        self.resize(980, 640)
        self._build_menu()
        self._build_ui()
        self._apply_window_flags()
        self._refresh_status()
        self._refresh_mappings()
        self._refresh_missing_types()

        self.sage_watch_timer = QTimer(self)
        self.sage_watch_timer.timeout.connect(self._watch_sage_process)
        self.sage_watch_timer.start(3000)

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

    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_invoice_tab(), "Facture")
        tabs.addTab(self._build_import_tab(), "Imports")
        tabs.addTab(self._build_settings_tab(), "Reglages")
        self.setCentralWidget(tabs)

    def _build_invoice_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Taper une reference Microstore...")
        self.search_input.textChanged.connect(self._on_search_changed)
        self.match_combo = QComboBox()
        self.match_combo.setMinimumWidth(360)
        self.match_combo.currentIndexChanged.connect(self._on_match_selected)
        self.quantity_mode = QComboBox()
        self.quantity_mode.addItems(["Pieces", "Colis"])
        self.quantity_value = QSpinBox()
        self.quantity_value.setRange(1, 99999)
        self.quantity_value.setValue(1)
        add_button = QPushButton("Ajouter ligne")
        add_button.clicked.connect(self._add_manual_line)

        search_row.addWidget(QLabel("Ref"))
        search_row.addWidget(self.search_input, 2)
        search_row.addWidget(self.match_combo, 3)
        search_row.addWidget(self.quantity_mode)
        search_row.addWidget(self.quantity_value)
        search_row.addWidget(add_button)
        layout.addLayout(search_row)

        self.product_info = QLabel("Aucun produit selectionne.")
        layout.addWidget(self.product_info)

        self.lines_table = QTableWidget(0, 10)
        self.lines_table.setHorizontalHeaderLabels(
            [
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
        )
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.lines_table.itemChanged.connect(self._on_line_item_changed)
        layout.addWidget(self.lines_table)

        actions = QHBoxLayout()
        remove_button = QPushButton("Supprimer ligne")
        remove_button.clicked.connect(self._remove_selected_line)
        inject_button = QPushButton("Preparer injection Sage")
        inject_button.clicked.connect(self._prepare_injection)
        actions.addWidget(remove_button)
        actions.addStretch(1)
        actions.addWidget(inject_button)
        layout.addLayout(actions)

        return page

    def _build_import_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        import_products_button = QPushButton("Importer BDD articles Microstore")
        import_products_button.clicked.connect(self._import_products)
        import_order_button = QPushButton("Importer fichier commande")
        import_order_button.clicked.connect(self._import_order)
        layout.addWidget(import_products_button)
        layout.addWidget(import_order_button)
        layout.addStretch(1)
        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.missing_types = QLabel("")
        layout.addWidget(self.missing_types)

        mapping_form = QFormLayout()
        self.mapping_type = QLineEdit()
        self.mapping_code = QLineEdit()
        self.mapping_label = QLineEdit()
        mapping_form.addRow("Type Microstore", self.mapping_type)
        mapping_form.addRow("Code Sage", self.mapping_code)
        mapping_form.addRow("Libelle Sage", self.mapping_label)
        save_mapping = QPushButton("Sauver mapping")
        save_mapping.clicked.connect(self._save_mapping)
        layout.addLayout(mapping_form)
        layout.addWidget(save_mapping)

        self.mapping_table = QTableWidget(0, 3)
        self.mapping_table.setHorizontalHeaderLabels(["Type Microstore", "Code Sage", "Libelle Sage"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.mapping_table.itemSelectionChanged.connect(self._load_selected_mapping)
        layout.addWidget(self.mapping_table)

        settings_form = QFormLayout()
        self.ahk_path = QLineEdit(self.settings.autohotkey_path)
        self.sage_path = QLineEdit(self.settings.sage_executable_path)
        self.window_title = QLineEdit(self.settings.sage_profile.window_title_contains)
        self.delay_ms = QSpinBox()
        self.delay_ms.setRange(10, 2000)
        self.delay_ms.setValue(self.settings.sage_profile.delay_ms)
        self.line_limit = QSpinBox()
        self.line_limit.setRange(0, 999)
        self.line_limit.setValue(self.settings.injection_line_limit)
        self.line_limit.setSpecialValueText("Toutes")
        self.step_mode = QCheckBox("Mode pas-a-pas AHK")
        self.step_mode.setChecked(self.settings.sage_profile.step_mode)
        self.auto_close = QCheckBox("Fermer l'assistant quand Sage se ferme")
        self.auto_close.setChecked(self.settings.auto_close_with_sage)
        settings_form.addRow("AutoHotkey.exe", self.ahk_path)
        settings_form.addRow("Sage.exe", self.sage_path)
        settings_form.addRow("Titre fenetre Sage contient", self.window_title)
        settings_form.addRow("Delai touches (ms)", self.delay_ms)
        settings_form.addRow("Limite lignes test", self.line_limit)
        settings_form.addRow("", self.step_mode)
        settings_form.addRow("", self.auto_close)
        layout.addLayout(settings_form)

        save_settings_button = QPushButton("Sauver reglages")
        save_settings_button.clicked.connect(self._save_app_settings)
        layout.addWidget(save_settings_button)
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

    def _refresh_mappings(self) -> None:
        mappings = self.db.list_mappings()
        self.mapping_table.setRowCount(len(mappings))
        for row, mapping in enumerate(mappings):
            self.mapping_table.setItem(row, 0, QTableWidgetItem(mapping.microstore_type))
            self.mapping_table.setItem(row, 1, QTableWidgetItem(mapping.sage_code))
            self.mapping_table.setItem(row, 2, QTableWidgetItem(mapping.sage_label))

    def _on_search_changed(self, text: str) -> None:
        self.current_matches = self.db.search_products(text)
        self.match_combo.blockSignals(True)
        self.match_combo.clear()
        for product in self.current_matches:
            self.match_combo.addItem(
                f"{product.ref} | {product.type_label} | {product.unit_price_ht or ''} | colis {product.package_size or ''}"
            )
        self.match_combo.blockSignals(False)
        self._on_match_selected(0)

    def _on_match_selected(self, index: int) -> None:
        if index < 0 or index >= len(self.current_matches):
            self.product_info.setText("Aucun produit selectionne.")
            return
        product = self.current_matches[index]
        self.product_info.setText(
            f"{product.ref} - {product.type_label} - prix {product.unit_price_ht or 'absent'} - colisage {product.package_size or 'absent'}"
        )

    def _add_manual_line(self) -> None:
        index = self.match_combo.currentIndex()
        if index < 0 or index >= len(self.current_matches):
            QMessageBox.warning(self, APP_NAME, "Selectionne une reference valide.")
            return
        product = self.current_matches[index]
        value = self.quantity_value.value()
        package_count = None
        quantity_pieces = value
        if self.quantity_mode.currentText() == "Colis":
            package_count = value
            quantity_pieces = value * (product.package_size or 0)
        line = self.resolver.line_from_product(
            product,
            quantity_pieces=quantity_pieces,
            package_count=package_count,
            source="manual",
        )
        self.lines.append(line)
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        self.lines_table.blockSignals(True)
        self.lines_table.setRowCount(len(self.lines))
        for row, line in enumerate(self.lines):
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
                if col in {0, 1, 3, 4, 6, 7, 9}:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.lines_table.setItem(row, col, item)
        self.lines_table.blockSignals(False)

    def _on_line_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row >= len(self.lines):
            return
        line = self.lines[row]
        try:
            line.sage_code = self.lines_table.item(row, 2).text().strip().upper()
            line.quantity_pieces = int(self.lines_table.item(row, 5).text().strip())
            price_text = self.lines_table.item(row, 8).text().strip().replace(",", ".")
            line.unit_price_ht = Decimal(price_text) if price_text else None
            line.price_confirmed = True
        except Exception as exc:
            line.validation_status = "blocked"
            line.validation_message = f"valeur invalide: {exc}"
        else:
            line.validate()
        self._refresh_lines()

    def _remove_selected_line(self) -> None:
        rows = sorted({item.row() for item in self.lines_table.selectedItems()}, reverse=True)
        for row in rows:
            if row < len(self.lines):
                del self.lines[row]
        self._refresh_lines()

    def _prepare_injection(self) -> None:
        if not self.lines:
            QMessageBox.information(self, APP_NAME, "Aucune ligne a injecter.")
            return
        for line in self.lines:
            line.validate()
        line_limit = self.settings.injection_line_limit
        selected_lines = self.lines[:line_limit] if line_limit > 0 else self.lines
        blocked = [line for line in selected_lines if line.validation_status != "ok"]
        if blocked:
            self._refresh_lines()
            QMessageBox.warning(self, APP_NAME, "Certaines lignes selectionnees sont bloquees. Corrige-les avant injection.")
            return
        try:
            path = write_injection_queue(
                self.lines,
                self.settings,
                line_limit=self.settings.injection_line_limit,
            )
            prepared_count = self.settings.injection_line_limit or len(self.lines)
            prepared_count = min(prepared_count, len(self.lines))
            self.db.log("injection_prepare", f"{prepared_count}/{len(self.lines)} lignes preparees: {path}")
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
        try:
            result = import_products(path)
            count = self.db.upsert_products(result.rows)  # type: ignore[arg-type]
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._refresh_status()
        self._refresh_missing_types()
        QMessageBox.information(self, APP_NAME, f"{count} references importees.\n" + "\n".join(result.warnings[:10]))

    def _import_order(self) -> None:
        path = self._pick_excel_file("Choisir commande Microstore")
        if not path:
            return
        try:
            result = import_order(path)
            new_lines = [self.resolver.line_from_order_row(row) for row in result.rows]  # type: ignore[arg-type]
            self.lines.extend(new_lines)
            self.db.log("order_import", f"{len(new_lines)} lignes commande importees depuis {path.name}")
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._refresh_lines()
        QMessageBox.information(self, APP_NAME, f"{len(new_lines)} lignes ajoutees.\n" + "\n".join(result.warnings[:10]))

    def _pick_excel_file(self, title: str) -> Path | None:
        filename, _ = QFileDialog.getOpenFileName(self, title, "", "Excel (*.xlsx *.xlsm *.xls)")
        return Path(filename) if filename else None

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
        self._refresh_mappings()
        self._refresh_missing_types()

    def _load_selected_mapping(self) -> None:
        rows = sorted({item.row() for item in self.mapping_table.selectedItems()})
        if not rows:
            return
        row = rows[0]
        self.mapping_type.setText(self.mapping_table.item(row, 0).text())
        self.mapping_code.setText(self.mapping_table.item(row, 1).text())
        self.mapping_label.setText(self.mapping_table.item(row, 2).text())

    def _save_app_settings(self) -> None:
        self.settings.autohotkey_path = self.ahk_path.text().strip() or "AutoHotkey64.exe"
        self.settings.sage_executable_path = self.sage_path.text().strip()
        self.settings.sage_profile.window_title_contains = self.window_title.text().strip() or "Sage"
        self.settings.sage_profile.delay_ms = self.delay_ms.value()
        self.settings.injection_line_limit = self.line_limit.value()
        self.settings.sage_profile.step_mode = self.step_mode.isChecked()
        self.settings.auto_close_with_sage = self.auto_close.isChecked()
        save_settings(self.settings)
        QMessageBox.information(self, APP_NAME, "Reglages sauvegardes.")

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
