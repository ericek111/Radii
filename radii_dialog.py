"""Configuration dialog for the Radii plugin."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .geodesic import segments_for_tolerance


class RadiiDialog(QDialog):
    def __init__(self, parent=None, initial=None):
        super().__init__(parent)
        self.setWindowTitle("Radii — geodesic circles from CSV")
        self.setMinimumWidth(520)

        initial = initial or {}

        self._format_label = QLabel(
            "<b>CSV columns:</b> <code>lat, lon, height, radius, color</code>"
            " &mdash; header row optional."
            "<ul style='margin-top:2px; margin-bottom:2px;'>"
            "<li><code>lat</code> / <code>lon</code>: decimal"
            " (<code>48.2082</code>) or DMS"
            " (<code>48 12 50.12</code>, <code>48°12'50.12\"N</code>,"
            " <code>48:12:50.12</code>)</li>"
            "<li><code>height</code>: WGS-84 ellipsoidal height, metres</li>"
            "<li><code>radius</code>: metres, positive</li>"
            "<li><code>color</code>: optional <code>rrggbb</code> or"
            " <code>#rrggbb[aa]</code>; may be left blank or omitted"
            " entirely</li>"
            "</ul>"
        )
        self._format_label.setWordWrap(True)
        self._format_label.setTextFormat(Qt.RichText)

        self._csv_edit = QLineEdit(initial.get("csv_path", ""))
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        csv_row = QHBoxLayout()
        csv_row.addWidget(self._csv_edit, 1)
        csv_row.addWidget(browse_btn)

        self._tolerance_spin = QDoubleSpinBox()
        self._tolerance_spin.setDecimals(3)
        self._tolerance_spin.setRange(0.001, 10000.0)
        self._tolerance_spin.setSingleStep(0.5)
        self._tolerance_spin.setSuffix(" m")
        self._tolerance_spin.setValue(float(initial.get("tolerance_m", 1.0)))
        self._tolerance_spin.valueChanged.connect(self._update_estimate_label)

        self._estimate_label = QLabel()
        self._estimate_label.setTextFormat(Qt.RichText)

        tol_row = QHBoxLayout()
        tol_row.addWidget(self._tolerance_spin)
        tol_row.addSpacing(12)
        tol_row.addWidget(self._estimate_label, 1)

        self._autoreload_chk = QCheckBox("Reload automatically when CSV changes")
        self._autoreload_chk.setChecked(bool(initial.get("autoreload", True)))

        self._show_centers_chk = QCheckBox("Also add a layer with center points")
        self._show_centers_chk.setChecked(bool(initial.get("show_centers", True)))

        self._default_color_edit = QLineEdit(initial.get("default_color", "#3388ff"))
        self._default_color_edit.setPlaceholderText("#rrggbb or #rrggbbaa")

        form = QFormLayout()
        form.addRow("CSV file:", csv_row)
        form.addRow("Accuracy tolerance:", tol_row)
        form.addRow("Default color:", self._default_color_edit)
        form.addRow(self._autoreload_chk)
        form.addRow(self._show_centers_chk)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self._format_label)
        root.addLayout(form)
        root.addWidget(buttons)

        self._update_estimate_label()

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CSV file", self._csv_edit.text() or "",
            "CSV files (*.csv *.txt);;All files (*)",
        )
        if path:
            self._csv_edit.setText(path)

    def _update_estimate_label(self):
        tol = self._tolerance_spin.value()
        n1 = segments_for_tolerance(1000.0, tol)
        n100 = segments_for_tolerance(100_000.0, tol)
        self._estimate_label.setText(
            f"<i>≈ <b>{n1}</b> segments at r=1&nbsp;km, "
            f"<b>{n100}</b> at r=100&nbsp;km</i>"
        )

    def values(self) -> dict:
        return {
            "csv_path": self._csv_edit.text().strip(),
            "tolerance_m": float(self._tolerance_spin.value()),
            "autoreload": self._autoreload_chk.isChecked(),
            "show_centers": self._show_centers_chk.isChecked(),
            "default_color": self._default_color_edit.text().strip() or "#3388ff",
        }
