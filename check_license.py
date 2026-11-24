
import os
import json

from app import DATA_DIR, license_manager
from licensing import get_machine_id, _LEDGER_TAMPERED

print("\n========== LICENSE DIAGNOSTICS ==========")

print("DATA DIR:", DATA_DIR)
print("Machine ID:", get_machine_id())

for filename in ["license.dat", "license.clk", "license.ses"]:
    path = os.path.join(str(DATA_DIR), filename)

    if os.path.exists(path):
        print(filename, "EXISTS")
        print("Size:", os.path.getsize(path), "bytes")
    else:
        print(filename, "MISSING")

license_path = license_manager.license_file

if os.path.exists(license_path):
    with open(license_path, "r") as f:
        data = json.load(f)

    print("\nSaved machine ID:", data.get("machine_id"))
    print("Machine ID matches:",
          data.get("machine_id") == get_machine_id())

    saved_fp = data.get("key_fingerprint")
    actual_fp = license_manager._fingerprint(
        data.get("license_key", "")
    )

    print("License fingerprint matches:",
          saved_fp == actual_fp)

ledger_ts, ledger_fp = license_manager._read_ledger()

print("\nLedger timestamp:", ledger_ts)
print("Ledger fingerprint:", ledger_fp)
print("Ledger tampered:", ledger_ts is _LEDGER_TAMPERED)

print("\nFull status:")
print(license_manager.check_license_status())

print("=========================================")
