"""Main plugin class — manages multiple CSV-backed circle configurations."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from qgis.PyQt.QtCore import QFileSystemWatcher, QTimer, QVariant
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
    QgsProperty,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsVectorLayer,
)

from .csv_parser import CsvParseError, RadiusPoint, load_csv
from .geodesic import geodesic_circle_vertices, segments_for_tolerance
from .radii_dialog import RadiiDialog


PROJECT_SCOPE = "Radii"


@dataclass
class RadiiInstance:
    """One CSV-driven configuration: its own file, style, and layers."""
    id: str
    csv_path: str
    tolerance_m: float = 1.0
    autoreload: bool = True
    show_centers: bool = True
    default_color: str = "#3388ff"
    circles_layer_id: Optional[str] = None
    centers_layer_id: Optional[str] = None

    def to_values(self) -> dict:
        return {
            "csv_path": self.csv_path,
            "tolerance_m": self.tolerance_m,
            "autoreload": self.autoreload,
            "show_centers": self.show_centers,
            "default_color": self.default_color,
        }

    def apply_values(self, values: dict) -> None:
        self.csv_path = values["csv_path"]
        self.tolerance_m = float(values["tolerance_m"])
        self.autoreload = bool(values["autoreload"])
        self.show_centers = bool(values["show_centers"])
        self.default_color = values["default_color"]


class RadiiPlugin:
    def __init__(self, iface):
        self.iface = iface
        self._action: Optional[QAction] = None

        self._instances: Dict[str, RadiiInstance] = {}
        self._watcher: Optional[QFileSystemWatcher] = None
        self._reload_timers: Dict[str, QTimer] = {}
        self._last_mtime: Dict[str, float] = {}

    # ------------------------------------------------------------------ QGIS

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self._action = QAction(icon, "Radii: geodesic circles from CSV…",
                               self.iface.mainWindow())
        self._action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu("&Radii", self._action)
        self.iface.addToolBarIcon(self._action)

        project = QgsProject.instance()
        project.readProject.connect(self._on_project_read)
        project.cleared.connect(self._on_project_cleared)
        QTimer.singleShot(0, self._restore_from_project)

    def unload(self):
        self._stop_watching()
        for t in self._reload_timers.values():
            t.stop()
        self._reload_timers.clear()
        try:
            project = QgsProject.instance()
            project.readProject.disconnect(self._on_project_read)
            project.cleared.disconnect(self._on_project_cleared)
        except (TypeError, RuntimeError):
            pass
        if self._action is not None:
            self.iface.removePluginVectorMenu("&Radii", self._action)
            self.iface.removeToolBarIcon(self._action)
            self._action = None

    def run(self):
        from .radii_manager import RadiiManagerDialog
        RadiiManagerDialog(self, self.iface.mainWindow()).exec_()

    # ------------------------------------------------------------------ public API used by the manager

    def instances(self) -> List[RadiiInstance]:
        return list(self._instances.values())

    def add_instance(self, values: dict) -> str:
        inst = RadiiInstance(id=uuid.uuid4().hex, csv_path=values["csv_path"])
        inst.apply_values(values)
        self._instances[inst.id] = inst
        self._rebuild_watcher()
        self._reload_instance(inst.id)
        self._save_to_project()
        return inst.id

    def update_instance(self, instance_id: str, values: dict) -> None:
        inst = self._instances.get(instance_id)
        if inst is None:
            return
        inst.apply_values(values)
        if not inst.show_centers and inst.centers_layer_id:
            self._remove_layer(inst.centers_layer_id)
            inst.centers_layer_id = None
        self._last_mtime.pop(inst.id, None)
        self._rebuild_watcher()
        self._reload_instance(inst.id)
        self._save_to_project()

    def remove_instance(self, instance_id: str) -> None:
        inst = self._instances.pop(instance_id, None)
        if inst is None:
            return
        if inst.circles_layer_id:
            self._remove_layer(inst.circles_layer_id)
        if inst.centers_layer_id:
            self._remove_layer(inst.centers_layer_id)
        t = self._reload_timers.pop(instance_id, None)
        if t is not None:
            t.stop()
            t.deleteLater()
        self._last_mtime.pop(instance_id, None)
        self._rebuild_watcher()
        self._save_to_project()

    # ------------------------------------------------------------------ persistence

    def _on_project_read(self, _doc=None):
        self._restore_from_project()

    def _on_project_cleared(self):
        self._stop_watching()
        for t in self._reload_timers.values():
            t.stop()
            t.deleteLater()
        self._reload_timers.clear()
        self._last_mtime.clear()
        self._instances.clear()

    def _restore_from_project(self):
        self._on_project_cleared()
        project = QgsProject.instance()

        def _read(key, default=""):
            v, ok = project.readEntry(PROJECT_SCOPE, key, default)
            return v if ok else default

        def _read_bool(key, default=False):
            v, ok = project.readBoolEntry(PROJECT_SCOPE, key, default)
            return v if ok else default

        def _read_double(key, default=0.0):
            v, ok = project.readDoubleEntry(PROJECT_SCOPE, key, default)
            return v if ok else default

        ids, _ok = project.readListEntry(PROJECT_SCOPE, "instance_ids", [])

        # Migration from the pre-multi-instance single-config format.
        if not ids and _read("csv_path"):
            migrated = RadiiInstance(
                id=uuid.uuid4().hex,
                csv_path=_read("csv_path"),
                tolerance_m=_read_double("tolerance_m", 1.0),
                autoreload=_read_bool("autoreload", True),
                show_centers=_read_bool("show_centers", True),
                default_color=_read("default_color", "#3388ff"),
                circles_layer_id=_read("circles_layer_id") or None,
                centers_layer_id=_read("centers_layer_id") or None,
            )
            self._instances[migrated.id] = migrated
        else:
            for iid in ids:
                p = f"instances/{iid}"
                csv_path = _read(f"{p}/csv_path")
                if not csv_path:
                    continue
                self._instances[iid] = RadiiInstance(
                    id=iid,
                    csv_path=csv_path,
                    tolerance_m=_read_double(f"{p}/tolerance_m", 1.0),
                    autoreload=_read_bool(f"{p}/autoreload", True),
                    show_centers=_read_bool(f"{p}/show_centers", True),
                    default_color=_read(f"{p}/default_color", "#3388ff"),
                    circles_layer_id=_read(f"{p}/circles_layer_id") or None,
                    centers_layer_id=_read(f"{p}/centers_layer_id") or None,
                )

        self._rebuild_watcher()
        for iid, inst in self._instances.items():
            if not os.path.exists(inst.csv_path):
                self._message(f"Radii: saved CSV not found — {inst.csv_path}",
                              Qgis.Warning)
                continue
            self._reload_instance(iid)
        # Persist the (possibly migrated) layout back.
        self._save_to_project()

    def _save_to_project(self):
        project = QgsProject.instance()
        # Clear legacy top-level keys so migrated entries don't come back.
        for legacy in ("csv_path", "tolerance_m", "autoreload",
                       "show_centers", "default_color",
                       "circles_layer_id", "centers_layer_id"):
            project.removeEntry(PROJECT_SCOPE, legacy)

        project.writeEntry(PROJECT_SCOPE, "instance_ids",
                           list(self._instances.keys()))
        for iid, inst in self._instances.items():
            p = f"instances/{iid}"
            project.writeEntry(PROJECT_SCOPE, f"{p}/csv_path", inst.csv_path)
            project.writeEntryDouble(PROJECT_SCOPE, f"{p}/tolerance_m",
                                     float(inst.tolerance_m))
            project.writeEntry(PROJECT_SCOPE, f"{p}/autoreload",
                               bool(inst.autoreload))
            project.writeEntry(PROJECT_SCOPE, f"{p}/show_centers",
                               bool(inst.show_centers))
            project.writeEntry(PROJECT_SCOPE, f"{p}/default_color",
                               inst.default_color)
            project.writeEntry(PROJECT_SCOPE, f"{p}/circles_layer_id",
                               inst.circles_layer_id or "")
            project.writeEntry(PROJECT_SCOPE, f"{p}/centers_layer_id",
                               inst.centers_layer_id or "")

    # ------------------------------------------------------------------ file watching

    def _rebuild_watcher(self):
        self._stop_watching()
        paths = set()
        dirs = set()
        for inst in self._instances.values():
            if not inst.autoreload or not inst.csv_path:
                continue
            if os.path.isfile(inst.csv_path):
                paths.add(inst.csv_path)
            parent = os.path.dirname(inst.csv_path) or "."
            if os.path.isdir(parent):
                dirs.add(parent)
        if not paths and not dirs:
            return
        self._watcher = QFileSystemWatcher()
        for p in paths:
            self._watcher.addPath(p)
        for d in dirs:
            self._watcher.addPath(d)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)

    def _stop_watching(self):
        if self._watcher is None:
            return
        try:
            self._watcher.fileChanged.disconnect()
            self._watcher.directoryChanged.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._watcher.deleteLater()
        self._watcher = None

    def _on_fs_event(self, changed_path):
        # Re-add file paths that disappeared (editors that rename-on-save).
        if self._watcher is not None:
            watched = set(self._watcher.files())
            for inst in self._instances.values():
                if (inst.autoreload and inst.csv_path
                        and os.path.isfile(inst.csv_path)
                        and inst.csv_path not in watched):
                    self._watcher.addPath(inst.csv_path)

        for inst in self._instances.values():
            if not inst.autoreload or not inst.csv_path:
                continue
            if not os.path.exists(inst.csv_path):
                continue
            parent = os.path.dirname(inst.csv_path) or "."
            if changed_path not in (inst.csv_path, parent):
                continue
            try:
                mtime = os.path.getmtime(inst.csv_path)
            except OSError:
                continue
            if self._last_mtime.get(inst.id) == mtime:
                continue
            self._last_mtime[inst.id] = mtime
            self._schedule_reload(inst.id)

    def _schedule_reload(self, instance_id: str):
        t = self._reload_timers.get(instance_id)
        if t is None:
            t = QTimer()
            t.setInterval(250)
            t.setSingleShot(True)
            t.timeout.connect(lambda i=instance_id: self._reload_instance(i))
            self._reload_timers[instance_id] = t
        t.start()

    # ------------------------------------------------------------------ reload

    def _reload_instance(self, instance_id: str):
        inst = self._instances.get(instance_id)
        if inst is None:
            return
        if not inst.csv_path or not os.path.exists(inst.csv_path):
            self._message(f"Radii: CSV not found: {inst.csv_path}",
                          Qgis.Warning)
            return
        try:
            points = load_csv(inst.csv_path)
        except CsvParseError as exc:
            self._message(
                f"Radii [{os.path.basename(inst.csv_path)}]: {exc}",
                Qgis.Critical,
            )
            return
        except OSError as exc:
            self._message(f"Radii: cannot read CSV — {exc}", Qgis.Critical)
            return
        try:
            self._last_mtime[inst.id] = os.path.getmtime(inst.csv_path)
        except OSError:
            pass

        self._render_instance(inst, points)
        self._save_to_project()
        self._message(
            f"Radii: loaded {len(points)} point(s) from "
            f"{os.path.basename(inst.csv_path)}",
            Qgis.Info,
        )

    # ------------------------------------------------------------------ rendering

    def _render_instance(self, inst: RadiiInstance, points: List[RadiusPoint]):
        basename = os.path.basename(inst.csv_path) or "radii"
        circles_name = f"Radii circles — {basename}"
        centers_name = f"Radii centers — {basename}"

        circles_layer = self._ensure_layer(
            inst.circles_layer_id, circles_name, "Polygon",
            [QgsField("row", QVariant.Int),
             QgsField("lat", QVariant.Double),
             QgsField("lon", QVariant.Double),
             QgsField("height", QVariant.Double),
             QgsField("radius_m", QVariant.Double),
             QgsField("color", QVariant.String),
             QgsField("segments", QVariant.Int)],
        )
        inst.circles_layer_id = circles_layer.id()
        if circles_layer.name() != circles_name:
            circles_layer.setName(circles_name)

        centers_layer = None
        if inst.show_centers:
            centers_layer = self._ensure_layer(
                inst.centers_layer_id, centers_name, "Point",
                [QgsField("row", QVariant.Int),
                 QgsField("lat", QVariant.Double),
                 QgsField("lon", QVariant.Double),
                 QgsField("height", QVariant.Double),
                 QgsField("radius_m", QVariant.Double),
                 QgsField("color", QVariant.String)],
            )
            inst.centers_layer_id = centers_layer.id()
            if centers_layer.name() != centers_name:
                centers_layer.setName(centers_name)
        elif inst.centers_layer_id:
            self._remove_layer(inst.centers_layer_id)
            inst.centers_layer_id = None

        self._replace_features(
            circles_layer,
            self._build_circle_features(points, inst.tolerance_m),
        )
        self._apply_per_feature_fill(circles_layer, inst.default_color)

        if centers_layer is not None:
            self._replace_features(
                centers_layer, self._build_center_features(points)
            )
            self._apply_per_feature_marker(centers_layer, inst.default_color)

        self.iface.mapCanvas().refreshAllLayers()

    def _build_circle_features(self, points, tolerance):
        feats = []
        for p in points:
            n = segments_for_tolerance(p.radius, tolerance)
            verts = geodesic_circle_vertices(p.lat, p.lon, p.radius, n)
            ring = [QgsPointXY(x, y) for x, y in verts]
            f = QgsFeature()
            f.setGeometry(QgsGeometry.fromPolygonXY([ring]))
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
        symbol = QgsFillSymbol.createSimple({
            "color": default_color,
            "outline_color": default_color,
            "outline_width": "0.4",
            "style": "solid",
        })
        base = QColor(default_color)
        base.setAlpha(60)
        symbol.setColor(base)

        fill_expr = (
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
                                  QgsProperty.fromExpression(fill_expr))
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
