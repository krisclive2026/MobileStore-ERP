"""
licensing.py  --  reusable RSA machine-locked licensing for ANY Flask product.
======================================================================
Drop this ONE file next to your app and add (example for a product "LABSOFT"):

    from licensing import LicenseManager
    lic = LicenseManager(
        product_id   = "LABSOFT",          # unique id for THIS software
        display_name = "LabSoft",
        data_dir     = DATA_DIR,           # writable folder for license files
        features     = {0x0001: "Appointments", 0x0002: "Backup"},   # optional
    )
    lic.install(app)                       # adds /api/license/* + blocks /api/* when unlicensed
    # optional per-route feature lock:   @lic.require_feature(0x0001)
    # in your __main__ block:            lic.startup()

Two kinds of v5 key:
  * product key   - bound to ONE product. A key made for product A is rejected by B.
  * BUNDLE key    - made with the keygen product "ALL-PRODUCTS"; accepted by EVERY
                    product that uses this module (one key for all your software).
Both are still locked to one machine. Only the PUBLIC key lives here; the PRIVATE
key stays in the developer keygen.

Key formats
-----------
  legacy / v2 / v3 / v4   original LabSoft keys. Accepted ONLY when
                          accept_legacy=True (LabSoft sets it, so every key
                          already issued to clients keeps working).
  v5 (this file)          10 expiry + 8 nonce + 4 product-code + 2 features
                          + 256 RSA signature. Product code is inside the
                          signed data.
Python 3.8+ compatible (needed for Windows 7 builds).
"""
import os
import json
import time
import hmac
import base64
import struct
import hashlib
import platform
import threading
import uuid
from datetime import datetime
from functools import wraps
from typing import Optional

from flask import request, jsonify
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# ── Shared public key (one key pair for ALL your software) ───────────────────
DEFAULT_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA14UifELm2Xaw1XMXR31z
DL9TlKM4FIt1CqfUH2zaxb2GxctWlv1QOFS9v+GAIKv6RvQ3EuRb5UcD2Qqi0pTu
ILJU2bEWiwbGegvkjkH3rQu3YXBiY4yVOASRS8tgbDMRAUE5F36BmBXXNElzRdvK
/fNe/jXcXanht9VlDPqpiZo5pYD557xfdJoxR2spNIUjjU0Z+rXabj5WbmxLb4ZY
BhRxvOgNEjNTEbRd/J0Y4wAy/KUBMQgp+YbiKg6DHZF73e0AXsskHu7czsskR2LC
dD0BSXWGiepVwK2p+XZjLX3DztIZ8AFKX43ULXE/ZKemyEErUoYyVsxCj5DlatdH
GQIDAQAB
-----END PUBLIC KEY-----"""

# Shared secret for the System Code (must match the keygen)
DEFAULT_SC_KEY = bytes.fromhex(
    "477a4b74394b346b6c613277787963320000000000000000000000000000001f")

V5_MAGIC = b"LSv5|"           # domain-separation prefix inside the signed data
ALL_FEATURES = 0xFFFF


class LicenseError(Exception):
    pass


class ProductMismatch(LicenseError):
    pass


# # ── Machine identity (same for every product on a PC) ────────────────────────
# def get_machine_id() -> str:
#     """Stable machine fingerprint from MAC address + hostname."""
#     mac = uuid.getnode()
#     raw = "%s:%s" % (mac, platform.node())
#     fp = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
#     return '-'.join(fp[i:i + 4] for i in range(0, 16, 4))
def get_machine_id() -> str:
    """Stable machine fingerprint using Windows MachineGuid."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography"
        ) as key:
            machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")

        raw = str(machine_guid).strip().lower()
        fp = hashlib.sha256(raw.encode()).hexdigest()[:16].upper()

        return "-".join(
            fp[i:i + 4] for i in range(0, 16, 4)
        )

    except Exception as e:
        raise RuntimeError(
            f"Unable to identify this Windows machine: {e}"
        )

def get_system_code(machine_id: str, sc_key: bytes = DEFAULT_SC_KEY) -> str:
    """Encrypt machine ID into a deterministic, copy-safe System Code."""
    data = machine_id.encode().ljust(32, b'\x00')
    enc = Cipher(algorithms.AES(sc_key), modes.ECB(), backend=default_backend()).encryptor()
    return base64.b32encode(enc.update(data) + enc.finalize()).decode().rstrip('=')


def decode_system_code(system_code: str, sc_key: bytes = DEFAULT_SC_KEY) -> str:
    padded = system_code.strip().upper()
    padded += '=' * (-len(padded) % 8)
    ct = base64.b32decode(padded)
    dec = Cipher(algorithms.AES(sc_key), modes.ECB(), backend=default_backend()).decryptor()
    return (dec.update(ct) + dec.finalize()).rstrip(b'\x00').decode()


def product_code(product_id: str) -> bytes:
    """4-byte code embedded in v5 keys, derived from the product id."""
    return hashlib.sha256(("PRODUCT:" + product_id.strip().upper()).encode()).digest()[:4]


# Reserved product id: a key made for it unlocks EVERY product (bundle key).
ALL_PRODUCTS_ID = "ALL-PRODUCTS"
ALL_PRODUCTS_CODE = product_code(ALL_PRODUCTS_ID)


_LEDGER_TAMPERED = object()


class LicenseManager(object):
    def __init__(self, product_id: str, display_name: str, data_dir: str,
                 features: Optional[dict] = None, accept_legacy: bool = False,
                 public_key_pem: bytes = DEFAULT_PUBLIC_KEY_PEM,
                 sc_key: bytes = DEFAULT_SC_KEY,
                 secret_tag: Optional[str] = None, cache_ttl: int = 30):
        self.product_id = product_id.strip().upper()
        if self.product_id == ALL_PRODUCTS_ID:
            raise ValueError("'%s' is reserved for bundle keys; choose another product_id." % ALL_PRODUCTS_ID)
        self.display_name = display_name
        self.data_dir = data_dir
        self.features = dict(features or {})
        self.accept_legacy = accept_legacy
        self.public_key_pem = public_key_pem
        self.sc_key = sc_key
        # LabSoft passes "labsoft" so ledgers made by older builds stay valid.
        self.secret_tag = (secret_tag or self.product_id.lower())
        self.cache_ttl = cache_ttl
        self.product_code = product_code(self.product_id)

        os.makedirs(data_dir, exist_ok=True)
        self.license_file = os.path.join(data_dir, "license.dat")
        self.clock_ledger = os.path.join(data_dir, "license.clk")
        self.session_file = os.path.join(data_dir, "license.ses")

        self._cache = {'ts': 0.0, 'status': None}
        self._lock = threading.RLock()

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _get_now() -> datetime:
        return datetime.utcnow()

    def system_code(self, machine_id: Optional[str] = None) -> str:
        return get_system_code(machine_id or get_machine_id(), self.sc_key)

    # ── clock ledger (tamper-evident last-seen timestamp + key fingerprint) ──
    def _ledger_secret(self) -> bytes:
        return hashlib.sha256(("LCK:%s:%s" % (get_machine_id(), self.secret_tag)).encode()).digest()

    def _read_ledger(self):
        if not os.path.exists(self.clock_ledger):
            return 0.0, None
        try:
            with open(self.clock_ledger, 'rb') as f:
                raw = f.read()
            if len(raw) < 40:
                return _LEDGER_TAMPERED, None
            stored, payload = raw[:32], raw[32:]
            expected = hmac.new(self._ledger_secret(), payload, hashlib.sha256).digest()
            if not hmac.compare_digest(stored, expected):
                return _LEDGER_TAMPERED, None
            ts = struct.unpack('d', payload[:8])[0]
            fp = payload[8:40].hex() if len(payload) >= 40 else None
            return ts, fp
        except Exception:
            return _LEDGER_TAMPERED, None

    def _write_ledger(self, ts: float, key_fingerprint: Optional[str] = None):
        try:
            ts_bytes = struct.pack('d', ts)
            fp_bytes = bytes.fromhex(key_fingerprint) if key_fingerprint else (b'\x00' * 32)
            payload = ts_bytes + fp_bytes
            mac = hmac.new(self._ledger_secret(), payload, hashlib.sha256).digest()
            with open(self.clock_ledger, 'wb') as f:
                f.write(mac + payload)
        except Exception:
            pass

    def _check_clock_rollback(self, now_dt: datetime, current_fp: Optional[str] = None) -> Optional[str]:
        now_ts = now_dt.timestamp()
        last_ts, stored_fp = self._read_ledger()
        tolerance = 86400  # 1 day (DST / time-zone changes)
        if last_ts is _LEDGER_TAMPERED:
            try:
                os.remove(self.clock_ledger)
            except Exception:
                pass
            self._write_ledger(now_ts, current_fp)
            return None
        if last_ts > 0 and (now_ts + tolerance) < last_ts:
            days = int((last_ts - now_ts) / 86400)
            return ("Clock rollback detected: system time is ~%d day(s) behind the last "
                    "recorded time. Please restore the correct system date and time." % days)
        fp_to_store = current_fp if current_fp is not None else stored_fp
        if now_ts > last_ts:
            self._write_ledger(now_ts, fp_to_store)
        return None

    # ── session counter ──────────────────────────────────────────────────────
    def _session_secret(self) -> bytes:
        return hashlib.sha256(("SES:%s:%s" % (get_machine_id(), self.secret_tag)).encode()).digest()

    def read_session_count(self) -> int:
        try:
            if not os.path.exists(self.session_file):
                return 0
            with open(self.session_file, 'rb') as f:
                raw = f.read()
            if len(raw) < 40:
                return 0
            mac, cb = raw[:32], raw[32:40]
            expected = hmac.new(self._session_secret(), cb, hashlib.sha256).digest()
            if not hmac.compare_digest(mac, expected):
                return 0
            return struct.unpack('Q', cb)[0]
        except Exception:
            return 0

    def increment_session_count(self) -> int:
        count = self.read_session_count() + 1
        try:
            cb = struct.pack('Q', count)
            mac = hmac.new(self._session_secret(), cb, hashlib.sha256).digest()
            with open(self.session_file, 'wb') as f:
                f.write(mac + cb)
        except Exception:
            pass
        return count

    # ── key decoding / verification ──────────────────────────────────────────
    def decode_key(self, license_key_b64: str) -> dict:
        """Return dict(version, expiry, nonce, features, features_bytes, product_code, signature)."""
        raw = base64.b64decode(license_key_b64)
        expiry = raw[:10].decode('ascii')
        rest = raw[10:]
        n = len(rest)
        if n == 256:      # legacy
            return dict(version=1, expiry=expiry, nonce=b'', features=None,
                        features_bytes=b'', product_code=None, signature=rest)
        if n == 264:      # v2
            return dict(version=2, expiry=expiry, nonce=rest[:8], features=None,
                        features_bytes=b'', product_code=None, signature=rest[8:])
        if n == 265:      # v3
            return dict(version=3, expiry=expiry, nonce=rest[:8], features=rest[8],
                        features_bytes=rest[8:9], product_code=None, signature=rest[9:])
        if n == 266:      # v4
            fb = rest[8:10]
            return dict(version=4, expiry=expiry, nonce=rest[:8],
                        features=struct.unpack('>H', fb)[0], features_bytes=fb,
                        product_code=None, signature=rest[10:])
        if n == 270:      # v5 (product-bound)
            fb = rest[12:14]
            return dict(version=5, expiry=expiry, nonce=rest[:8],
                        features=struct.unpack('>H', fb)[0], features_bytes=fb,
                        product_code=rest[8:12], signature=rest[14:])
        raise ValueError("Invalid key length: %d bytes" % len(raw))

    def rsa_verify(self, machine_id: str, license_key_b64: str) -> dict:
        k = self.decode_key(license_key_b64)
        if k['version'] == 5:
            if k['product_code'] not in (self.product_code, ALL_PRODUCTS_CODE):
                raise ProductMismatch("Key belongs to a different product.")
            payload = (V5_MAGIC + ("%s:%s:" % (machine_id, k['expiry'])).encode()
                       + k['nonce'] + k['product_code'] + k['features_bytes'])
        else:
            if not self.accept_legacy:
                raise LicenseError("Old-format key is not valid for this product.")
            if k['features'] is not None:
                payload = ("%s:%s:" % (machine_id, k['expiry'])).encode() + k['nonce'] + k['features_bytes']
            elif k['nonce']:
                payload = ("%s:%s:" % (machine_id, k['expiry'])).encode() + k['nonce']
            else:
                payload = ("%s:%s" % (machine_id, k['expiry'])).encode()
        pub = serialization.load_pem_public_key(self.public_key_pem, backend=default_backend())
        pub.verify(k['signature'], payload, padding.PKCS1v15(), hashes.SHA256())
        return k

    @staticmethod
    def _fingerprint(license_key_b64: str) -> str:
        return hashlib.sha256(license_key_b64.encode()).hexdigest()

    def verify_license_key(self, machine_id: str, license_key_b64: str):
        """Returns (valid, expiry, message)."""
        try:
            if not os.path.exists(self.license_file):
                return False, None, "No license file found."
            with open(self.license_file, 'r') as f:
                data = json.load(f)
            license_key = data.get('license_key', license_key_b64)
            if data.get('machine_id', '') != machine_id:
                return False, None, "License is not valid for this machine."
            k = self.rsa_verify(machine_id, license_key)
            expiry = k['expiry']
            now_dt = self._get_now()
            err = self._check_clock_rollback(now_dt)
            if err:
                return False, expiry, "\u26a0\ufe0f " + err
            if now_dt.date() > datetime.strptime(expiry, "%Y-%m-%d").date():
                return False, expiry, "License has expired. Please request a renewal key."
            data['last_check'] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            with open(self.license_file, 'w') as f:
                json.dump(data, f)
            return True, expiry, "License is valid."
        except Exception as e:
            return False, None, "License verification failed: %s" % e

    def activate_license(self, machine_id: str, license_key_b64: str):
        """Returns (success, message)."""
        with self._lock:
            try:
                k = self.rsa_verify(machine_id, license_key_b64)
                expiry = k['expiry']
                new_fp = self._fingerprint(license_key_b64)

                _, ledger_fp = self._read_ledger()
                if ledger_fp and ledger_fp == new_fp:
                    return False, "This license key has already been used. Please request a new key."
                if os.path.exists(self.license_file):
                    try:
                        with open(self.license_file, 'r') as f:
                            if json.load(f).get('key_fingerprint') == new_fp:
                                return False, "This license key has already been used. Please request a new key."
                    except Exception:
                        pass

                now_dt = self._get_now()
                err = self._check_clock_rollback(now_dt, new_fp)
                if err:
                    return False, "\u26a0\ufe0f " + err
                if now_dt.date() > datetime.strptime(expiry, "%Y-%m-%d").date():
                    return False, "The license key has already expired."

                stamp = now_dt.strftime("%Y-%m-%d %H:%M:%S")
                with open(self.license_file, 'w') as f:
                    json.dump({"machine_id": machine_id, "license_key": license_key_b64,
                               "key_fingerprint": new_fp, "expiry": expiry,
                               "activated_at": stamp, "last_check": stamp}, f)
                self._write_ledger(now_dt.timestamp(), new_fp)
                self._cache.update(ts=0.0, status=None)
                return True, "License activated successfully!"
            except ProductMismatch:
                return False, "This license key is for a different product."
            except LicenseError:
                return False, ("This key format is not valid for %s. Please request a new key."
                               % self.display_name)
            except Exception:
                return False, "Activation failed: Invalid key or wrong machine."

    # ── status ───────────────────────────────────────────────────────────────
    @staticmethod
    def _bad(mid, msg, clock_ok=False):
        return {"valid": False, "machine_id": mid, "expiry": None, "days_left": 0,
                "message": msg, "expired": False, "clock_ok": clock_ok}

    def check_license_status(self) -> dict:
        mid = get_machine_id()
        ledger_ts, ledger_fp = self._read_ledger()
        ledger_exists = os.path.exists(self.clock_ledger)
        license_exists = os.path.exists(self.license_file)

        if ledger_ts is _LEDGER_TAMPERED:
            if not license_exists:
                try:
                    os.remove(self.clock_ledger)
                except Exception:
                    pass
                return self._bad(mid, "No license found. Please activate.", True)
            return self._bad(mid, "\u26a0\ufe0f License integrity check failed. Please contact support to reactivate.")
        if ledger_fp and not license_exists:
            return self._bad(mid, "\u26a0\ufe0f License file has been removed. Please contact support to reactivate.")
        if ledger_fp and license_exists:
            try:
                with open(self.license_file, 'r') as f:
                    dat = json.load(f)
                if dat.get('key_fingerprint') != ledger_fp:
                    return self._bad(mid, "\u26a0\ufe0f License file has been modified. Please contact support to reactivate.")
            except Exception:
                return self._bad(mid, "\u26a0\ufe0f License file is corrupted. Please contact support to reactivate.")
        if license_exists and not ledger_exists:
            return self._bad(mid, "\u26a0\ufe0f License clock file has been removed. Please contact support to reactivate.")
        if not ledger_exists and not license_exists:
            return self._bad(mid, "No license found. Please activate.", True)

        try:
            with open(self.license_file, 'r') as f:
                data = json.load(f)
            valid, expiry, msg = self.verify_license_key(mid, data.get('license_key', ''))
            days_left, expired = 0, False
            low = msg.lower()
            clock_ok = "rollback" not in low and "clock" not in low and "integrity" not in low
            if expiry:
                try:
                    now_dt = self._get_now()
                    exp_dt = datetime.strptime(expiry, "%Y-%m-%d")
                    days_left = max(0, (exp_dt - now_dt).days)
                    expired = now_dt.date() > exp_dt.date()
                except Exception:
                    pass
            return {"valid": valid, "machine_id": mid, "expiry": expiry, "days_left": days_left,
                    "message": msg, "expired": expired, "clock_ok": clock_ok}
        except Exception as e:
            return self._bad(mid, "License check error: %s" % e, True)

    def cached_status(self) -> dict:
        """Serialised + cached (valid results only) so parallel requests never race on license.dat."""
        with self._lock:
            now = time.time()
            if self._cache['status'] and (now - self._cache['ts']) < self.cache_ttl:
                return self._cache['status']
            status = self.check_license_status()
            if status.get('valid'):
                self._cache.update(ts=now, status=status)
            else:
                self._cache.update(ts=0.0, status=None)
            return status

    # ── feature gating ───────────────────────────────────────────────────────
    def get_active_features(self) -> int:
        """Active feature bitmask. Legacy/v2 keys, no license, or any error -> ALL_FEATURES."""
        try:
            if not os.path.exists(self.license_file):
                return ALL_FEATURES
            with open(self.license_file, 'r') as f:
                key = json.load(f).get('license_key', '')
            if not key:
                return ALL_FEATURES
            feats = self.decode_key(key)['features']
            return ALL_FEATURES if feats is None else feats
        except Exception:
            return ALL_FEATURES

    def has_feature(self, bit: int) -> bool:
        return bool(self.get_active_features() & bit)

    def _locked(self, labels: str):
        return jsonify({
            'error': 'feature_locked', 'feature': labels,
            'message': "%s requires an upgraded license key. Contact your provider to unlock it." % labels
        }), 403

    def require_feature(self, bit: int):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                if not self.has_feature(bit):
                    return self._locked(self.features.get(bit, 'This feature'))
                return fn(*a, **kw)
            return wrapper
        return decorator

    def require_any_feature(self, *bits: int):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                active = self.get_active_features()
                if not any(active & b for b in bits):
                    return self._locked(" or ".join(self.features.get(b, 'This feature') for b in bits))
                return fn(*a, **kw)
            return wrapper
        return decorator

    # ── Flask integration ────────────────────────────────────────────────────
    def install(self, app, unprotected=('index', 'static')):
        """Register /api/license/* + /api/session/count and block every other /api/* route
        while the license is invalid."""
        own = {'license_status', 'license_activate', 'license_machine_id', 'get_session_count'}
        allowed = set(unprotected) | own

        @app.before_request
        def _enforce_license():
            endpoint = request.endpoint or ''
            if endpoint in allowed or not request.path.startswith('/api/'):
                return None
            status = self.cached_status()
            if not status.get('valid'):
                return jsonify({'error': 'license_invalid',
                                'message': status.get('message', 'License is not valid.')}), 403
            return None

        def license_status():
            status = self.check_license_status()
            if status.get('machine_id'):
                status['system_code'] = self.system_code(status['machine_id'])
            status['features'] = self.get_active_features()
            status['product'] = self.display_name
            return jsonify(status)

        def license_activate():
            data = request.get_json(silent=True) or {}
            key = str(data.get('license_key', '')).strip()
            if not key:
                return jsonify({'success': False, 'message': 'License key is required.'}), 400
            ok, msg = self.activate_license(get_machine_id(), key)
            return jsonify({'success': ok, 'message': msg})

        def license_machine_id():
            return jsonify({'machine_id': get_machine_id()})

        def get_session_count():
            return jsonify({'count': self.read_session_count()})

        app.add_url_rule('/api/license/status', 'license_status', license_status, methods=['GET'])
        app.add_url_rule('/api/license/activate', 'license_activate', license_activate, methods=['POST'])
        app.add_url_rule('/api/license/machine-id', 'license_machine_id', license_machine_id, methods=['GET'])
        app.add_url_rule('/api/session/count', 'get_session_count', get_session_count, methods=['GET'])
        return self

    def startup(self):
        """Call once from __main__: counts a session when the license is valid."""
        if self.check_license_status().get('valid'):
            self.increment_session_count()
