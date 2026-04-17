"""Manager dialog — lists all Radii CSV configurations in the project."""

from __future__ import annotations

import os
from typing import Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .radii_dialog import RadiiDialog


class RadiiManagerDialog(QDialog):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self.setWindowTitle("Radii — CSV configurations")
        self.setMinimumSize(620, 320)

        header = QLabel(
            "Each row below is an independent CSV file rendered as its own"
            " pair of layers. Add, edit, or remove configurations freely —"
            " they're saved with the project."
        )
        header.setWordWrap(True)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["CSV file", "Tolerance", "Auto-reload", "Status"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._table.itemDoubleClicked.connect(self._on_edit)
        self._table.itemSelectionChanged.connect(self._update_button_state)

        self._add_btn = QPushButton("Add…")
        self._edit_btn = QPushButton("Edit…")
        self._remove_btn = QPushButton("Remove")
        self._reload_btn = QPushButton("Reload now")
        self._add_btn.clicked.connect(self._on_add)
        self._edit_btn.clicked.connect(self._on_edit)
        self._remove_btn.clicked.connect(self._on_remove)
        self._reload_btn.clicked.connect(self._on_reload)

        btn_col = QVBoxLayout()
        btn_col.addWidget(self._add_btn)
        btn_col.addWidget(self._edit_btn)
        btn_col.addWidget(self._remove_btn)
        btn_col.addSpacing(8)
        btn_col.addWidget(self._reload_btn)
        btn_col.addStretch(1)

        center = QHBoxLayout()
        center.addWidget(self._table, 1)
        center.addLayout(btn_col)

        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.accept)
        close_box.accepted.connect(self.accept)

        root = QVBoxLayout(self)
        root.addWidget(header)
        root.addLayout(center)
        root.addWidget(close_box)

        self._refresh()

    # ----------------------------------------------------------------- helpers

    def _refresh(self):
        instances = self._plugin.instances()
        self._table.setRowCount(len(instances))
        for i, inst in enumerate(instances):
            name = os.path.basename(inst.csv_path) if inst.csv_path else "<unset>"
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(inst.csv_path)
            name_item.setData(Qt.UserRole, inst.id)
            self._table.setItem(i, 0, name_item)
            self._table.setItem(i, 1, QTableWidgetItem(f"{inst.tolerance_m:g} m"))
            self._table.setItem(
                i, 2, QTableWidgetItem("yes" if inst.autoreload else "no")
            )
            if not inst.csv_path:
                status = "no file"
            elif os.path.exists(inst.csv_path):
                status = "OK"
            else:
                status = "MISSING"
            self._table.setItem(i, 3, QTableWidgetItem(status))
        self._update_button_state()

    def _update_button_state(self):
        has_selection = self._selected_instance() is not None
        self._edit_btn.setEnabled(has_selection)
        self._remove_btn.setEnabled(has_selection)
        self._reload_btn.setEnabled(has_selection)

    def _selected_instance(self):
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        iid = item.data(Qt.UserRole)
        for inst in self._plugin.instances():
            if inst.id == iid:
                return inst
        return None

    # ----------------------------------------------------------------- slots

    def _on_add(self):
        dlg = RadiiDialog(self)
        if not dlg.exec_():
            return
        values = dlg.values()
        if not values["csv_path"]:
            QMessageBox.information(self, "Radii", "No CSV file selected.")
            return
        self._plugin.add_instance(values)
        self._refresh()

    def _on_edit(self):
        inst = self._selected_instance()
        if inst is None:
            return
        dlg = RadiiDialog(self, initial=inst.to_values())
        if not dlg.exec_():
            return
        values = dlg.values()
        if not values["csv_path"]:
            QMessageBox.information(self, "Radii", "No CSV file selected.")
            return
        self._plugin.update_instance(inst.id, values)
        self._refresh()

    def _on_remove(self):
        inst = self._selected_instance()
        if inst is None:
            return
        label = os.path.basename(inst.csv_path) or "this configuration"
        ret = QMessageBox.question(
            self, "Remove configuration",
            f"Remove Radii circles for {label}?\n"
            "This will delete the associated circle and center layers.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self._plugin.remove_instance(inst.id)
        self._refresh()

    def _on_reload(self):
        inst = self._selected_instance()
        if inst is None:
            return
        self._plugin._reload_instance(inst.id)
        self._refresh()
