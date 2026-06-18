"""FabricFinder GUI — one-click launcher (no console window on Windows).
Double-click this file in Explorer to start the app.
Python must be installed and the .venv set up (run setup.ps1 first).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui import App

App().mainloop()
