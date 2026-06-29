"""Entry point: launch the PDF Editor GUI.

    python main.py [optional-file.pdf]
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        try:
            win.ctrl.open(sys.argv[1])
            win.page_spin.setMaximum(win.ctrl.page_count)
            win.page_spin.setValue(1)
            win._load_page()
            win._refresh_actions()
        except Exception:  # noqa: BLE001 - best-effort CLI open
            pass
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
