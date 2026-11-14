"""Run the inventory-only executable with python -m telescreen.inventory_service."""

from telescreen.inventory_service.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
