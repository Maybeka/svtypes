"""Native Qt Widgets exploration prototype; no WebEngine or source writeback.

Run with the repository environment after installing PySide6-Essentials.
The existing isolated scanner supplies the same catalog as the web GUI.
This is a direction-setting example, not a replacement for the web editor.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for source in (ROOT / "python", ROOT / "extensions/svtypes_design_manifest/src",
               ROOT / "extensions/svtypes_coverage_tools/src", HERE):
    sys.path.insert(0, str(source))

try:
    from PySide6.QtCore import Qt, QRectF, Signal
    from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QUndoCommand, QUndoStack
    from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout,
        QLabel, QLineEdit, QMainWindow, QPushButton, QSplitter, QTableWidget,
        QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
        QPlainTextEdit, QHeaderView, QAbstractItemView)
except ImportError as exc:
    raise SystemExit("Native prototype requires PySide6-Essentials in this Python environment.") from exc

from svtypes_coverage_tools import GuiSession


def expression(value):
    if not isinstance(value, dict):
        return str(value)
    kind = value.get("kind")
    if kind == "constant":
        return str(value["value"])
    if kind == "name":
        return value["name"]
    if kind == "field":
        return value["path"]
    if kind == "attribute":
        return expression(value["base"]) + "." + value["name"]
    if kind == "range":
        return expression(value["lower"]) + ":" + expression(value["upper"])
    if kind == "values":
        return ", ".join(map(expression, value["items"]))
    if kind == "enum_literal":
        return str(value.get("name", value.get("value", "enum")))
    if kind == "cross_bin_refs":
        return " × ".join(f"{item['point']}.{item['bin']}" for item in value["items"])
    if kind == "binary":
        return f"({expression(value['left'])} {value['operator']} {expression(value['right'])})"
    if kind == "compare":
        return expression(value["left"]) + " " + " ".join(
            op + " " + expression(v) for op, v in zip(value["operators"], value["comparators"]))
    if kind == "subscript":
        return expression(value["base"]) + "[" + expression(value["index"]) + "]"
    return f"<{kind or 'selector'}>"


def ranges(selector):
    kind = selector.get("kind")
    if kind == "constant" and type(selector.get("value")) is int:
        return [(selector["value"], selector["value"])]
    if kind == "range":
        lo, hi = ranges(selector["lower"]), ranges(selector["upper"])
        if len(lo) == len(hi) == 1 and lo[0][0] == lo[0][1] and hi[0][0] == hi[0][1]:
            return [(lo[0][0], hi[0][0])]
    if kind == "values":
        parts = [ranges(v) for v in selector["items"]]
        if parts and all(parts):
            return [pair for part in parts for pair in part]
    return []


def from_ranges(parts):
    items = [({"kind": "constant", "value": a} if a == b else
              {"kind": "range", "lower": {"kind": "constant", "value": a},
               "upper": {"kind": "constant", "value": b}}) for a, b in sorted(parts)]
    return items[0] if len(items) == 1 else {"kind": "values", "items": items}


def parse_selector(text, domain):
    text = text.strip().removeprefix("{").removesuffix("}")
    parts = []
    for fragment in text.split(","):
        fragment = fragment.strip().removeprefix("[").removesuffix("]")
        values = fragment.split(":")
        if len(values) not in (1, 2):
            raise ValueError("Use integers or closed ranges, e.g. {1, [4:5], 9}.")
        numbers = [int(v.strip(), 0) for v in values]
        a, b = numbers[0], numbers[-1]
        if not domain[0] <= a <= b <= domain[1]:
            raise ValueError(f"Range must lie within {domain[0]}…{domain[1]}.")
        parts.append((a, b))
    return from_ranges(parts)


def selector_text(selector):
    parts = ranges(selector)
    if parts:
        return "{" + ", ".join(str(a) if a == b else f"[{a}:{b}]" for a, b in sorted(parts)) + "}"
    return expression(selector)


def bin_text(bin_):
    if bin_["kind"] == "transition":
        return " → ".join(selector_text(s) for s in bin_["selector"].get("items", []))
    if bin_["kind"] == "default":
        return "default"
    return selector_text(bin_["selector"])


class Highlight(QSyntaxHighlighter):
    def highlightBlock(self, text):
        rules = [(r"\b(class|lambda|pass)\b", "#8839ef"),
                 (r"\b(CovPoint|Cross)\b", "#df8e1d"),
                 (r"\b(source|iff|self)\b", "#d20f39"),
                 (r"\b(bins|transition_bins|ignore_bins|illegal_bins|default_bins)\b", "#5c5f77"),
                 (r"\b\d+\b", "#fe640b"), (r"[=:@]", "#179299")]
        for pattern, color in rules:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class SelectorInput(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.clearFocus()
            return
        super().keyPressEvent(event)


class Track(QWidget):
    """One shared native range editor for ordinary and transition selectors."""
    preview = Signal(object)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(90)
        self.parts = []
        self.domain = (0, 15)
        self.drag = None
        self.setMouseTracking(True)

    def box(self):
        return QRectF(12, 20, max(1, self.width() - 24), 30)

    def x(self, value):
        r = self.box()
        a, b = self.domain
        return r.left() + (value - a) / (b - a + 1) * r.width()

    def value(self, x):
        a, b = self.domain
        r = self.box()
        return max(a, min(b, a + int((x - r.left()) / r.width() * (b - a + 1))))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.box()
        painter.setPen(QColor("#dce0e8"))
        painter.setBrush(QColor("#eff1f5"))
        painter.drawRoundedRect(r, 8, 8)
        a, b = self.domain
        for start, end in self.parts:
            left, right = self.x(start), self.x(end + 1)
            rect = QRectF(left, r.top(), max(4, right - left), r.height())
            painter.setPen(QColor("#8839ef"))
            painter.setBrush(QColor("#dcd0f5"))
            painter.drawRoundedRect(rect, 7, 7)
            for edge in (left + 4, right - 4):
                painter.drawLine(int(edge), int(r.top() + 7), int(edge), int(r.bottom() - 7))
        painter.setPen(QColor("#6c6f85"))
        ticks = list(range(a, b + 1)) if b - a < 24 else [a, (a + b) // 2, b]
        for value in ticks:
            center = (self.x(value) + self.x(value + 1)) / 2
            painter.drawText(QRectF(center - 36, 58, 72, 22), Qt.AlignmentFlag.AlignCenter, str(value))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.box().contains(event.position()):
            return
        x = event.position().x()
        for index, (a, b) in enumerate(self.parts):
            left, right = self.x(a), self.x(b + 1)
            if left - 6 <= x <= right + 6:
                edge = "lo" if abs(x - left) <= 9 else "hi" if abs(x - right) <= 9 else "move"
                self.drag = (index, edge, self.value(x), list(self.parts))
                self.grabMouse()
                return

    def mouseMoveEvent(self, event):
        if not self.drag:
            return
        index, edge, origin, initial = self.drag
        a, b = initial[index]
        value = self.value(event.position().x())
        if edge == "lo":
            a = min(value, b)
        elif edge == "hi":
            b = max(value, a)
        else:
            shift = max(self.domain[0] - a, min(self.domain[1] - b, value - origin))
            a, b = a + shift, b + shift
        self.parts = list(initial)
        self.parts[index] = (a, b)
        self.preview.emit(from_ranges(self.parts))
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag and event.button() == Qt.MouseButton.LeftButton:
            self.releaseMouse()
            self.drag = None
            self.finished.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.box().contains(event.position()):
            value = self.value(event.position().x())
            self.parts.append((value, value))
            self.preview.emit(from_ranges(self.parts))
            self.drag = (len(self.parts) - 1, "hi", value, list(self.parts))
            self.grabMouse()
            self.update()


class Edit(QUndoCommand):
    def __init__(self, window, before, after):
        super().__init__("修改 bin 草稿")
        self.window, self.before, self.after = window, deepcopy(before), deepcopy(after)

    def apply(self, bins):
        self.window.bins[:] = deepcopy(bins)
        self.window.refresh()
        self.window.show_target()

    def undo(self):
        self.apply(self.before)

    def redo(self):
        self.apply(self.after)


STYLE = """
QMainWindow, QWidget#workspace {background:#eff1f5; color:#4c4f69;}
QWidget {font-size:13px; color:#4c4f69;}
QFrame#card {background:white; border:1px solid #dce0e8; border-radius:10px;}
QLabel#heading {font-size:24px; font-weight:600; color:#303446;}
QLabel#section {font-size:15px; font-weight:600;}
QLabel#muted {color:#8c8fa1;}
QTreeWidget, QTableWidget {background:white; border:0; outline:0;}
QTreeWidget::item {height:29px;}
QTreeWidget::item:selected, QTableWidget::item:selected {background:#e6ddf7; color:#6830b5;}
QHeaderView::section {background:#f7f8fb; padding:8px; border:0; color:#6c6f85;}
QLineEdit {background:#f7f8fb; border:1px solid #dce0e8; border-radius:5px; padding:6px;}
QLineEdit:focus {border:1px solid #8839ef;}
QPushButton {background:#f7f8fb; border:1px solid #dce0e8; border-radius:6px; padding:7px 12px;}
QPushButton:checked {background:#e6ddf7; border-color:#8839ef; color:#6830b5;}
QPushButton:hover {background:#e6ddf7;}
QPlainTextEdit {background:#eff1f5; border:0; padding:9px; color:#4c4f69;}
QComboBox {padding:5px; border:1px solid #dce0e8; border-radius:5px;}
"""


class Window(QMainWindow):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.drafts, self.stacks = {}, {}
        self.node, self.bins = {}, []
        self.index, self.step = -1, 0
        self.checkpoint = []
        self.setWindowTitle("SvTypes · 原生覆盖率工作台 / 方向原型")
        self.resize(1240, 840)
        base = QWidget(objectName="workspace")
        self.setCentralWidget(base)
        outer = QVBoxLayout(base)
        outer.setContentsMargins(20, 16, 20, 16)
        title = QHBoxLayout()
        title.addWidget(QLabel("SvTypes   /   Coverage Studio", objectName="section"))
        title.addStretch()
        title.addWidget(QLabel("原生 Qt Widgets · 仅页内草稿 · 不写源码", objectName="muted"))
        outer.addLayout(title)
        split = QSplitter()
        outer.addWidget(split)
        sidebar = QFrame(objectName="card")
        side = QVBoxLayout(sidebar)
        self.search = QLineEdit(placeholderText="筛选覆盖项…")
        side.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        side.addWidget(self.tree)
        split.addWidget(sidebar)
        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(14, 0, 0, 0)
        self.heading = QLabel("选择覆盖项", objectName="heading")
        self.subtitle = QLabel("来自现有设计扫描器；不读取运行覆盖率数据。", objectName="muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.heading)
        layout.addWidget(self.subtitle)
        card = QFrame(objectName="card")
        content = QVBoxLayout(card)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Bins", objectName="section"))
        bar.addStretch()
        for text, callback in [("撤销", self.undo), ("重做", self.redo), ("放弃所有修改", self.reset)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            bar.addWidget(button)
        content.addLayout(bar)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["名称", "分类", "选择器 / 交叉组合"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 100)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        content.addWidget(self.table, 1)
        self.nodes = QHBoxLayout()
        content.addLayout(self.nodes)
        self.track = Track()
        self.track.preview.connect(self.preview)
        self.track.finished.connect(self.commit)
        content.addWidget(self.track)
        editrow = QHBoxLayout()
        editrow.addWidget(QLabel("名称"))
        self.name = QLineEdit()
        self.name.returnPressed.connect(self.rename)
        editrow.addWidget(self.name)
        self.kind = QComboBox()
        self.kind.addItems(["normal", "ignore", "illegal"])
        self.kind.activated.connect(self.change_kind)
        editrow.addWidget(self.kind)
        editrow.addWidget(QLabel("· 选择器"))
        self.selector = SelectorInput()
        self.selector.textEdited.connect(self.text_preview)
        self.selector.returnPressed.connect(self.confirm_text)
        self.selector.cancelled.connect(self.cancel_text)
        editrow.addWidget(self.selector, 2)
        content.addLayout(editrow)
        self.hint = QLabel("", objectName="muted")
        self.hint.setWordWrap(True)
        content.addWidget(self.hint)
        layout.addWidget(card, 3)
        dslcard = QFrame(objectName="card")
        dslbox = QVBoxLayout(dslcard)
        dslbox.addWidget(QLabel("DSL  ·  原始声明 + 选中 bin 草稿", objectName="section"))
        self.dsl = QPlainTextEdit()
        self.dsl.setReadOnly(True)
        self.dsl.setFont(QFont("Menlo", 12))
        self.highlighter = Highlight(self.dsl.document())
        dslbox.addWidget(self.dsl)
        layout.addWidget(dslcard, 2)
        split.addWidget(right)
        split.setSizes([270, 930])
        self.tree.currentItemChanged.connect(self.open_node)
        self.table.itemSelectionChanged.connect(self.select_bin)
        self.search.textChanged.connect(self.filter_tree)
        from PySide6.QtGui import QKeySequence, QShortcut
        self.shortcuts = [QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo),
                          QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo)]
        owners = {}
        for group in session.catalog["groups"]:
            owner = group["owner_type"]
            if owner not in owners:
                owners[owner] = QTreeWidgetItem(self.tree, [owner.split(".")[-1]])
            root = QTreeWidgetItem(owners[owner], [group["declaration_name"]])
            for field, prefix in [("points", "○"), ("crosses", "×")]:
                for node in group[field]:
                    item = QTreeWidgetItem(root, [f"{prefix}  {node['name']}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, node["semantic_id"])
        self.tree.expandAll()
        initial = next((g for g in session.catalog["groups"] if g["declaration_name"] == "cg"), None)
        if initial:
            wanted = initial["points"][0]["semantic_id"]
            def visit(item):
                if item.data(0, Qt.ItemDataRole.UserRole) == wanted:
                    self.tree.setCurrentItem(item)
                for i in range(item.childCount()):
                    visit(item.child(i))
            for i in range(self.tree.topLevelItemCount()):
                visit(self.tree.topLevelItem(i))

    def filter_tree(self, text):
        def visit(item):
            child_matches = [visit(item.child(i)) for i in range(item.childCount())]
            match = text.lower() in item.text(0).lower() or any(child_matches)
            item.setHidden(not match)
            return match
        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def open_node(self, item, previous=None):
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not key:
            return
        if self.node:
            self.commit()
        result = self.session.node(key)
        self.node, self.node_kind = result["node"], result["kind"]
        self.bins = self.drafts.setdefault(key, deepcopy(self.node["bins"]))
        self.stack = self.stacks.setdefault(key, QUndoStack(self))
        self.index, self.step = -1, 0
        self.checkpoint = deepcopy(self.bins)
        self.heading.setText(self.node["name"])
        self.subtitle.setText(("Cross · 具体交叉 bins（原型只读）" if self.node_kind == "cross" else
                              "CovPoint · " + expression(self.node.get("expression"))) + "\n" + key)
        self.refresh()
        if self.bins:
            self.table.blockSignals(True)
            self.table.selectRow(0)
            self.table.blockSignals(False)
            self.select_bin()
        else:
            self.select_bin()

    def refresh(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.bins))
        for i, bin_ in enumerate(self.bins):
            for j, text in enumerate((bin_["name"], bin_["kind"], bin_text(bin_))):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                self.table.setItem(i, j, cell)
        if 0 <= self.index < len(self.bins):
            self.table.selectRow(self.index)
        self.table.blockSignals(False)
        self.update_dsl()

    def current(self):
        return self.bins[self.index] if 0 <= self.index < len(self.bins) else None

    def target(self):
        bin_ = self.current()
        if not bin_:
            return None
        if bin_["kind"] == "transition":
            steps = bin_["selector"].get("items", [])
            return steps[self.step] if 0 <= self.step < len(steps) else None
        return bin_["selector"]

    def select_bin(self):
        requested_row = self.table.currentRow()
        if self.node:
            self.commit()
        self.index = requested_row
        self.table.blockSignals(True)
        if requested_row >= 0:
            self.table.selectRow(requested_row)
        self.table.blockSignals(False)
        self.step = 0
        self.show_target()

    def show_target(self):
        while self.nodes.count():
            item = self.nodes.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        bin_ = self.current()
        if not bin_:
            self.track.hide()
            self.selector.setEnabled(False)
            self.name.setEnabled(False)
            self.kind.setEnabled(False)
            return
        self.name.setText(bin_["name"])
        self.kind.setCurrentText(bin_.get("coverage_kind", bin_["kind"]))
        if bin_["kind"] == "transition":
            for i, step in enumerate(bin_["selector"].get("items", [])):
                button = QPushButton(selector_text(step))
                button.setCheckable(True)
                button.setChecked(i == self.step)
                button.clicked.connect(lambda checked=False, n=i: self.pick_step(n))
                self.nodes.addWidget(button)
                if i + 1 < len(bin_["selector"]["items"]):
                    self.nodes.addWidget(QLabel("→"))
            self.nodes.addStretch()
        target = self.target()
        parts = ranges(target) if target else []
        domain = self.node.get("options", {}).get("comparison_domain", {})
        width = domain.get("width", 4)
        bounds = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if domain.get("signed") else (0, (1 << width) - 1)
        editable = self.node_kind == "point" and bool(parts) and "[" not in bin_["name"]
        self.track.domain = bounds
        # Keep wide-type tracks focused on the existing selector, without changing its comparison domain.
        if parts and bounds[1] - bounds[0] > 255:
            self.track.domain = (max(bounds[0], min(a for a, b in parts) - 4), min(bounds[1], max(b for a, b in parts) + 4))
        self.bounds = bounds
        self.track.parts = parts
        self.track.setVisible(editable)
        self.track.update()
        self.selector.setEnabled(editable)
        self.name.setEnabled(editable and "[" not in bin_["name"])
        self.kind.setEnabled(editable and bin_["kind"] != "transition")
        self.selector.setText(selector_text(target) if target else "")
        self.checkpoint = deepcopy(self.bins)
        self.hint.setText("拖动片选或两端调整范围；双击轨道新增片选。输入立即预览，Enter 确认，Esc 放弃本次编辑。" if editable else
                          "该构造仅展示：cross、automatic/参数化/重复 transition 等尚未接入原生编辑。")
        self.update_dsl()

    def pick_step(self, index):
        self.commit()
        self.step = index
        self.show_target()

    def preview(self, selector):
        target = self.target()
        if target is None:
            return
        target.clear()
        target.update(selector)
        self.selector.setText(selector_text(selector))
        self.refresh()

    def text_preview(self, text):
        try:
            selector = parse_selector(text, self.bounds)
        except (ValueError, TypeError) as exc:
            self.hint.setText(str(exc))
            return
        target = self.target()
        target.clear()
        target.update(selector)
        self.track.parts = ranges(selector)
        self.track.update()
        self.refresh()

    def confirm_text(self):
        try:
            parse_selector(self.selector.text(), self.bounds)
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        self.commit()
        self.selector.clearFocus()

    def cancel_text(self):
        self.bins[:] = deepcopy(self.checkpoint)
        self.refresh()
        self.show_target()

    def commit(self):
        if self.bins != self.checkpoint:
            self.stack.push(Edit(self, self.checkpoint, self.bins))
        self.checkpoint = deepcopy(self.bins)

    def rename(self):
        text = self.name.text().strip()
        if not re.fullmatch(r"[A-Za-z_]\w*", text) or any(b["name"] == text and b is not self.current() for b in self.bins):
            self.hint.setText("名称须为唯一标识符。")
            return
        self.current()["name"] = text
        self.commit()

    def change_kind(self):
        bin_ = self.current()
        if bin_["kind"] == "transition":
            bin_["coverage_kind"] = self.kind.currentText()
        else:
            bin_["kind"] = self.kind.currentText()
        self.commit()

    def undo(self):
        if self.node:
            self.commit()
            self.stack.undo()

    def redo(self):
        if self.node:
            self.stack.redo()

    def reset(self):
        if self.node:
            self.bins[:] = deepcopy(self.node["bins"])
            self.stack.clear()
            self.refresh()
            self.show_target()

    def update_dsl(self):
        source = self.node.get("source", "# 无可用的原始声明源码")
        bin_ = self.current()
        draft = ""
        if bin_ and self.node_kind == "point" and "[" not in bin_["name"]:
            family = {"ignore": "ignore_bins", "illegal": "illegal_bins", "default": "default_bins"}.get(bin_["kind"], "bins")
            if bin_["kind"] == "transition":
                family = "transition_bins"
                body = ", ".join(expression(s) for s in bin_["selector"].get("items", []))
            else:
                body = expression(bin_["selector"])
            draft = f"\n\n# 选中 bin 的页内草稿（不是写回源码）\n{bin_['name']} = {family}" + (f"[{body}]" if family != "default_bins" else "")
        self.dsl.setPlainText(source + draft)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", default="packet")
    parser.add_argument("--source-root", type=Path, default=HERE)
    parser.add_argument("--python-path", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    # Uses the launching environment, not an embedded replacement project runtime.
    session = GuiSession(args.source_root, (args.source_root, ROOT / "python",
        ROOT / "extensions/svtypes_design_manifest/src", ROOT / "extensions/svtypes_coverage_tools/src", *args.python_path))
    session.scan([args.module])
    window = Window(session)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
