"""Google Drive, Docs, Sheets and domain-directory collector integrations."""

from telescreen.collectors.google.adapter import GoogleAdapter, build_google_adapters
from telescreen.collectors.google.admin import AdminApi
from telescreen.collectors.google.auth import GoogleAuth, ServiceAccountKey
from telescreen.collectors.google.drive import DriveApi, DriveChange, DriveFile, DrivePage
from telescreen.collectors.google.sheets import SheetsApi, SheetValues
from telescreen.collectors.google.sweep import GoogleSweep

__all__: list[str] = [
    "AdminApi",
    "DriveApi",
    "DriveChange",
    "DriveFile",
    "DrivePage",
    "GoogleAdapter",
    "GoogleAuth",
    "GoogleSweep",
    "ServiceAccountKey",
    "SheetValues",
    "SheetsApi",
    "build_google_adapters",
]
