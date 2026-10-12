"""Restrained desktop design system: two intentionally balanced palettes."""

from PySide6.QtGui import QColor, QPalette

PALETTES = {
    "dark": {
        "window": "#151B21",
        "panel": "#1C242B",
        "field": "#212C34",
        "subtle": "#25313A",
        "border": "#35414B",
        "text": "#EFF2F4",
        "muted": "#A3B0BA",
        "accent": "#E6B45D",
        "accent_text": "#1B2024",
        "hover": "#2A3843",
        "selected": "#33434B",
        "green": "#70C0A5",
        "error": "#EB897E",
        "light_border": "#465460",
    },
    "light": {
        "window": "#F0F2F3",
        "panel": "#FFFFFF",
        "field": "#FAFBFB",
        "subtle": "#E9EFF1",
        "border": "#CED5D9",
        "text": "#202A30",
        "muted": "#52626C",
        "accent": "#A76E12",
        "accent_text": "#FFFFFF",
        "hover": "#EFF2F5",
        "selected": "#E3EDF1",
        "green": "#217A5F",
        "error": "#B54137",
        "light_border": "#B8C4CA",
    },
}


def palette(mode: str) -> dict[str, str]:
    return PALETTES.get(mode, PALETTES["dark"])


def apply_theme(app, mode: str) -> None:
    c = palette(mode)
    qpalette = QPalette()
    qpalette.setColor(QPalette.ColorRole.Window, QColor(c["window"]))
    qpalette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    qpalette.setColor(QPalette.ColorRole.Base, QColor(c["field"]))
    qpalette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["subtle"]))
    qpalette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    qpalette.setColor(QPalette.ColorRole.Button, QColor(c["panel"]))
    qpalette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    qpalette.setColor(QPalette.ColorRole.Highlight, QColor(c["selected"]))
    qpalette.setColor(QPalette.ColorRole.HighlightedText, QColor(c["text"]))
    app.setPalette(qpalette)
    app.setStyleSheet(f"""
    QWidget {{ color: {c["text"]}; font-size: 12px; }}
    QWidget#root {{ background: {c["window"]}; }}
    QFrame#header, QFrame#footer {{ background: {c["panel"]}; }}
    QFrame#side {{ background: {c["panel"]}; border-right: 1px solid {c["border"]}; }}
    QFrame#content {{ background: {c["window"]}; }}
    QFrame#divider {{ background: {c["border"]}; }}
    QLabel#productName {{ font-weight: 700; font-size: 17px; letter-spacing: .2px; }}
    QLabel#brandLine {{ color: {c["muted"]}; font-size: 9px; letter-spacing: 1.5px; }}
    QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
    QLabel#pageSubtitle {{ color: {c["muted"]}; font-size: 12px; }}
    QLabel#eyebrow, QLabel#caption {{ color: {c["muted"]}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
    QLabel#fieldName {{ font-weight: 600; font-size: 11px; }}
    QLabel#sectionName {{ font-weight: 700; font-size: 13px; }}
    QLabel#number {{ color: {c["accent"]}; font-weight: 700; font-size: 11px; }}
    QLabel#helper, QLabel#secondaryText {{ color: {c["muted"]}; font-size: 11px; }}
    QLabel#statusOK {{ color: {c["green"]}; }}
    QLabel#statusError {{ color: {c["error"]}; }}
    QFrame#infoStrip {{ background: {c["subtle"]}; border: 1px solid {c["border"]}; border-radius: 5px; }}
    QFrame#imageWell, QFrame#writeStage {{ background: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: 5px; }}
    QLabel#writeStep {{ color: {c["muted"]}; background: {c["subtle"]}; border: 1px solid {c["border"]}; border-radius: 4px; padding: 7px 5px; font-weight: 600; font-size: 10px; }}
    QLabel#writeStep[stepStatus="active"] {{ color: {c["accent_text"]}; background: {c["accent"]}; border-color: {c["accent"]}; }}
    QLabel#writeStep[stepStatus="done"] {{ color: {c["green"]}; border-color: {c["green"]}; }}
    QLabel#writeStep[stepStatus="failed"] {{ color: {c["error"]}; border-color: {c["error"]}; }}
    QLabel#writeStep[stepStatus="stopped"] {{ color: {c["muted"]}; border-color: {c["light_border"]}; }}
    QLabel#writePercent {{ color: {c["accent"]}; font-weight: 700; font-size: 14px; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ border: none; background: {c["window"]}; width: 8px; margin: 1px 0; }}
    QScrollBar::handle:vertical {{ background: {c["border"]}; border-radius: 3px; min-height: 26px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QPushButton {{ background: {c["panel"]}; border: 1px solid {c["border"]}; padding: 7px 12px; border-radius: 4px; min-height: 21px; font-weight: 600; }}
    QPushButton:hover:enabled {{ background: {c["hover"]}; border-color: {c["light_border"]}; }}
    QPushButton:pressed {{ background: {c["selected"]}; }}
    QPushButton:disabled {{ color: {c["muted"]}; background: {c["subtle"]}; border-color: {c["border"]}; }}
    QPushButton#primaryButton {{ background: {c["accent"]}; color: {c["accent_text"]}; border: 1px solid {c["accent"]}; }}
    QPushButton#primaryButton:hover:enabled {{ background: {c["accent"]}; }}
    QPushButton#osRow {{ text-align: left; border: 0; border-left: 3px solid transparent; border-radius: 0; padding: 5px 8px; min-height: 43px; background: transparent; font-weight: 600; }}
    QPushButton#osRow:hover {{ background: {c["hover"]}; }}
    QPushButton#osRow[active="true"] {{ background: {c["selected"]}; border-left: 3px solid {c["accent"]}; }}
    QComboBox {{ background: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: 4px; min-height: 33px; padding: 0 9px; }}
    QComboBox:disabled {{ color: {c["muted"]}; background: {c["subtle"]}; }}
    QComboBox QAbstractItemView {{ background: {c["panel"]}; border: 1px solid {c["border"]}; selection-background-color: {c["selected"]}; }}
    QTableWidget {{ background: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: 4px; gridline-color: {c["border"]}; selection-background-color: {c["selected"]}; }}
    QTableWidget::item {{ padding: 5px; }}
    QHeaderView::section {{ background: {c["subtle"]}; color: {c["muted"]}; padding: 7px; font-weight: 600; font-size: 10px; border: none; border-bottom: 1px solid {c["border"]}; }}
    QProgressBar {{ border: 1px solid {c["border"]}; background: {c["subtle"]}; border-radius: 3px; height: 7px; text-align: center; }}
    QProgressBar::chunk {{ background: {c["accent"]}; border-radius: 2px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; }}
    QToolButton {{ border: 1px solid {c["border"]}; border-radius: 4px; padding: 7px 10px; background: {c["panel"]}; }}
    QToolButton:hover {{ background: {c["hover"]}; }}
    QMessageBox, QFileDialog {{ background: {c["panel"]}; }}
    """)
