"""Main plugin class — wires dialog, CSV watcher and QGIS layers."""

from __future__ import annotations

import os
from typing import List, Optional

from qgis.PyQt.QtCore import QFileSystemWatcher, QTimer
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

from .csv_parser import CsvParseError, RadiusPoint, load_csv
from .geodesic import geodesic_circle_vertices, segments_for_tolerance
from .radii_dialog import RadiiDialog


CIRCLES_LAYER_NAME = "Radii — geodesic circles"
CENTERS_LAYER_NAME = "Radii — centers"


class RadiiPlugin:
    def __init__(self, iface):
        self.iface = iface
        self._action: Optional[QAction] = None
        self._watcher: Optional[QFileSystemWatcher] = None
        self._reload_timer = QTimer()
        self._reload_timer.setInterval(250)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._reload_now)

        self._settings = {
            "csv_path": "",
            "tolerance_m": 1.0,
            "autoreload": True,
            "show_centers": True,
            "default_color": "#3388ff",
        }
        self._circles_layer_id: Optional[str] = None
        self._centers_layer_id: Optional[str] = None

    # ------------------------------------------------------------------ QGIS

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self._action = QAction(icon, "Radii: geodesic circles from CSV…",
                               self.iface.mainWindow())
        self._action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu("&Radii", self._action)
        self.iface.addToolBarIcon(self._action)

    def unload(self):
        self._stop_watching()
        if self._action is not None:
            self.iface.removePluginVectorMenu("&Radii", self._action)
            self.iface.removeToolBarIcon(self._action)
            self._action = None

    # ------------------------------------------------------------------ run

    def run(self):
        dlg = RadiiDialog(self.iface.mainWindow(), initial=self._settings)
        if not dlg.exec_():
            return
        self._settings = dlg.values()
        if not self._settings["csv_path"]:
            self._message("Radii: no CSV file selected", Qgis.Warning)
            return
        self._start_watching(self._settings["csv_path"])
        self._reload_now()

    # ------------------------------------------------------------------ file watching

    def _start_watching(self, path: str):
        self._stop_watching()
        if not self._settings["autoreload"]:
            return
        self._watcher = QFileSystemWatcher()
        self._watcher.addPath(path)
        # Watch the parent directory too — editors often rename-on-save which
        # removes the original file from the watch list.
        parent = os.path.dirname(path) or "."
        self._watcher.addPath(parent)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)

    def _stop_watching(self):
        if self._watcher is not None:
            try:
                self._watcher.fileChanged.disconnect()
                self._watcher.directoryChanged.disconnect()
            except Exception:
                pass
            self._watcher.deleteLater()
            self._watcher = None

    def _on_fs_event(self, _path):
        # Re-add the file path in case it was replaced atomically.
        csv_path = self._settings["csv_path"]
        if self._watcher is not None and csv_path:
            if csv_path not in self._watcher.files() and os.path.exists(csv_path):
                self._watcher.addPath(csv_path)
        self._reload_timer.start()

    # ------------------------------------------------------------------ reload

    def _reload_now(self):
        path = self._settings["csv_path"]
        if not path or not os.path.exists(path):
            self._message(f"Radii: CSV not found: {path}", Qgis.Warning)
            return
        try:
            points = load_csv(path)
        except CsvParseError as exc:
            self._message(f"Radii: CSV error — {exc}", Qgis.Critical)
            return
        except OSError as exc:
            self._message(f"Radii: cannot read CSV — {exc}", Qgis.Critical)
            return

        self._render(points)
        self._message(
            f"Radii: loaded {len(points)} point(s) from {os.path.basename(path)}",
            Qgis.Info,
        )

    # ------------------------------------------------------------------ rendering

    def _render(self, points: List[RadiusPoint]):
        tolerance = float(self._settings["tolerance_m"])
        default_color = self._settings["default_color"]

        circles_layer = self._ensure_layer(
            self._circles_layer_id, CIRCLES_LAYER_NAME, "Polygon",
            [QgsField("row", QVariant.Int),
             QgsField("lat", QVariant.Double),
             QgsField("lon", QVariant.Double),
             QgsField("height", QVariant.Double),
             QgsField("radius_m", QVariant.Double),
             QgsField("color", QVariant.String),
             QgsField("segments", QVariant.Int)],
        )
        self._circles_layer_id = circles_layer.id()

        centers_layer = None
        if self._settings["show_centers"]:
            centers_layer = self._ensure_layer(
                self._centers_layer_id, CENTERS_LAYER_NAME, "Point",
                [QgsField("row", QVariant.Int),
                 QgsField("lat", QVariant.Double),
                 QgsField("lon", QVariant.Double),
                 QgsField("height", QVariant.Double),
                 QgsField("radius_m", QVariant.Double),
                 QgsField("color", QVariant.String)],
            )
            self._centers_layer_id = centers_layer.id()
        elif self._centers_layer_id:
            self._remove_layer(self._centers_layer_id)
            self._centers_layer_id = None

        self._replace_features(circles_layer, self._build_circle_features(points, tolerance))
        self._apply_per_feature_fill(circles_layer, default_color)

        if centers_layer is not None:
            self._replace_features(centers_layer, self._build_center_features(points))
            self._apply_per_feature_marker(centers_layer, default_color)

        self.iface.mapCanvas().refreshAllLayers()

    def _build_circle_features(self, points, tolerance):
        feats = []
        for p in points:
            n = segments_for_tolerance(p.radius, tolerance)
            verts = geodesic_circle_vertices(p.lat, p.lon, p.radius, n)
            ring = [QgsPointXY(x, y) for x, y in verts]
            geom = QgsGeometry.fromPolygonXY([ring])
            f = QgsFeature()
            f.setGeometry(geom)
            f.setAttributes([
                p.row_index, p.lat, p.lon, p.height, p.radius,
                p.color or "", n,
            ])
            feats.append(f)
        return feats

    def _build_center_features(self, points):
        feats = []
        for p in points:
            f = QgsFeature()
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(p.lon, p.lat)))
            f.setAttributes([
                p.row_index, p.lat, p.lon, p.height, p.radius, p.color or "",
            ])
            feats.append(f)
        return feats

    def _ensure_layer(self, existing_id, name, geom_type, fields):
        project = QgsProject.instance()
        layer = project.mapLayer(existing_id) if existing_id else None
        if layer is not None:
            return layer

        uri = f"{geom_type}?crs=EPSG:4326"
        layer = QgsVectorLayer(uri, name, "memory")
        if not layer.isValid():
            raise RuntimeError(f"failed to create memory layer {name!r}")
        layer.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        provider = layer.dataProvider()
        provider.addAttributes(fields)
        layer.updateFields()
        project.addMapLayer(layer)
        return layer

    def _remove_layer(self, layer_id):
        project = QgsProject.instance()
        if project.mapLayer(layer_id) is not None:
            project.removeMapLayer(layer_id)

    def _replace_features(self, layer, features):
        layer.startEditing()
        provider = layer.dataProvider()
        existing_ids = [f.id() for f in layer.getFeatures()]
        if existing_ids:
            provider.deleteFeatures(existing_ids)
        provider.addFeatures(features)
        layer.commitChanges()
        layer.updateExtents()

    def _apply_per_feature_fill(self, layer, default_color):
        """Render each feature with its own ``color`` attribute.

        Uses a data-defined ``fill_color`` on a single symbol so styling stays
        fast even with many features.
        """
        symbol = QgsFillSymbol.createSimple({
            "color": default_color,
            "outline_color": default_color,
            "outline_width": "0.4",
            "style": "solid",
        })
        # Semi-transparent fill by default
        base = QColor(default_color)
        base.setAlpha(60)
        symbol.setColor(base)

        from qgis.core import QgsProperty, QgsSymbolLayer
        expr = (
            f"CASE WHEN \"color\" IS NOT NULL AND \"color\" <> '' "
            f"THEN set_color_part(\"color\", 'alpha', 60) "
            f"ELSE set_color_part('{default_color}', 'alpha', 60) END"
        )
        stroke_expr = (
            f"CASE WHEN \"color\" IS NOT NULL AND \"color\" <> '' "
            f"THEN \"color\" ELSE '{default_color}' END"
        )
        sl = symbol.symbolLayer(0)
        sl.setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor,
                                  QgsProperty.fromExpression(expr))
        sl.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeColor,
                                  QgsProperty.fromExpression(stroke_expr))
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    def _apply_per_feature_marker(self, layer, default_color):
        symbol = QgsMarkerSymbol.createSimple({
            "name": "circle",
            "size": "2.2",
            "color": default_color,
            "outline_color": "black",
            "outline_width": "0.2",
        })
        from qgis.core import QgsProperty, QgsSymbolLayer
        expr = (
            f"CASE WHEN \"color\" IS NOT NULL AND \"color\" <> '' "
            f"THEN \"color\" ELSE '{default_color}' END"
        )
        sl = symbol.symbolLayer(0)
        sl.setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor,
                                  QgsProperty.fromExpression(expr))
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    # ------------------------------------------------------------------ ui

    def _message(self, text, level=Qgis.Info):
        bar = self.iface.messageBar()
        bar.pushMessage("Radii", text, level=level, duration=6)
