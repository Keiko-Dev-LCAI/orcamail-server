#!/usr/bin/env python3
"""
OrcaMail Backend Server
Wallet-to-wallet encrypted messaging on Lightchain blockchain.

Architecture:
  - Messages are encrypted client-side with the recipient's secp256k1 public key (ECIES)
  - Backend stores encrypted blobs — it cannot read them
  - On-chain: payment + MailSent event via OrcaMail contract
  - Port: 8181

Run:
  python3 /home/keiko/Desktop/orcamail-server.py

  Or via systemd:
  sudo systemctl start orcamail-server
"""

import sys

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os

# Local pylibs override (only active when running on the original PC)
_local_pylibs = '/home/keiko/pylibs'
if os.path.isdir(_local_pylibs):
    sys.path.insert(0, _local_pylibs)
import uuid
import time
import re
import hashlib
import smtplib
import threading
import urllib.request
import secrets
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse, parse_qs, quote as url_quote

# ════════════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════════════

# PORT: Railway injects $PORT automatically; local default is 8181
PORT              = int(os.environ.get("PORT", 8181))
ORCAMAIL_CONTRACT = "0x5Fd3918Bb85685A006287eEa34988026f0eC9989"  # v2 — Lightchain mainnet, chain ID 9200
LCAI_RPC          = "https://rpc.mainnet.lightchain.ai"

# DATA_DIR: point to your Railway persistent volume mount (e.g. /data).
# Defaults to the home directory so existing local installs keep working.
DATA_DIR          = os.environ.get("DATA_DIR", os.path.expanduser("~"))
DATA_FILE         = os.path.join(DATA_DIR, "orcamail-messages.json")
STATS_FILE        = os.path.join(DATA_DIR, "orcamail-data.json")
PUBKEYS_FILE      = os.path.join(DATA_DIR, "orcamail-pubkeys.json")  # address → secp256k1 pubkey for ECIES
OPTINS_FILE       = os.path.join(DATA_DIR, "orcamail-optins.json")   # address → {preferences, ts} (server-side fallback)
SENDS_FILE        = os.path.join(DATA_DIR, "orcamail-sends.json")    # address → {sends_used, sub_expiry}
FREE_SENDS_LIMIT  = 5
FRONTEND_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orcamail-v2", "orcamail-v2.html")
# SMTP — set as Railway env vars (never hard-code credentials)
SMTP_HOST         = os.environ.get("SMTP_HOST", "")        # e.g. "smtp.gmail.com"
SMTP_PORT         = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER         = os.environ.get("SMTP_USER", "")        # e.g. "orcamail@gmail.com"
SMTP_PASS         = os.environ.get("SMTP_PASS", "")        # app password
NOTIFY_FROM       = os.environ.get("NOTIFY_FROM", "orcamail@orcamail.ai")

SERVER_START_TIME = int(time.time())
MAINTENANCE_FLAG  = os.path.join(DATA_DIR, "MAINTENANCE_MODE")

_ORCAMAIL_MAINTENANCE_HTML = b"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OrcaMail - Coming Soon</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{background:#0a1628;color:#e8f4f8;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      min-height:100vh;display:flex;align-items:center;justify-content:center}
    .c{text-align:center;max-width:540px;padding:48px 32px}
    .icon{width:80px;height:80px;
      background:linear-gradient(135deg,#00d4ff,#0077aa);
      border-radius:22px;display:inline-flex;align-items:center;
      justify-content:center;font-size:40px;margin-bottom:20px;
      box-shadow:0 0 48px rgba(0,212,255,.25)}
    h1{font-size:2.4rem;font-weight:700;color:#00d4ff;margin-bottom:10px}
    .sub{font-size:1rem;color:#7ab0c5;margin-bottom:32px}
    .card{background:rgba(0,212,255,.05);
      border:1px solid rgba(0,212,255,.18);
      border-radius:14px;padding:28px 32px;
      font-size:1rem;color:#c8dde8;line-height:1.75}
    .dot{display:inline-block;width:9px;height:9px;
      border-radius:50%;background:#00d4ff;margin-right:10px;
      vertical-align:middle;animation:blink 1.8s ease-in-out infinite}
    @keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
    .foot{margin-top:28px;font-size:.82rem;color:#3d6e80}
  </style>
</head>
<body>
  <div class="c">
    <div class="icon">&#x1F40B;</div>
    <h1>OrcaMail</h1>
    <p class="sub">Wallet-to-wallet encrypted messaging</p>
    <div class="card">
      <span class="dot"></span><strong>Coming Soon</strong><br><br>
      We&rsquo;re rebuilding OrcaMail for a better, more private experience.
      Improved end-to-end encryption, a faster interface, and deeper
      Lightchain integration are on the way.<br><br>Check back soon.
    </div>
    <p class="foot">orcamail.ai &nbsp;&middot;&nbsp; Maintenance in progress</p>
  </div>
</body>
</html>
"""

# ════════════════════════════════════════════════════════════════════════════
# ABI — minimal OrcaMail contract interface
# ════════════════════════════════════════════════════════════════════════════

ORCAMAIL_ABI = [
    # v2 ABI — update ORCAMAIL_CONTRACT above to new address after deploying OrcaMail_v2.sol
    {
        "name": "hasOptedIn",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "wallet", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getPreferences",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "wallet", "type": "address"}],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name": "isSubscribed",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "wallet", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "freeSendsRemaining",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "wallet", "type": "address"}],
        "outputs": [{"name": "", "type": "uint8"}],
    },
]

# ════════════════════════════════════════════════════════════════════════════
# HELPERS — validation & persistence
# ════════════════════════════════════════════════════════════════════════════

ETH_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

def is_valid_address(addr: str) -> bool:
    return bool(addr and ETH_ADDR_RE.match(addr))

def normalize_address(addr: str) -> str:
    """Lowercase hex address for consistent keying."""
    return addr.lower()


_data_lock = threading.Lock()

def load_messages() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_messages(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_stats() -> dict:
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {
            "total_messages": 0,
            "total_opted_in": 0,
            "opted_in_cache": {},
        }

def save_stats(data: dict):
    with open(STATS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_pubkeys() -> dict:
    """Load stored secp256k1 public keys for ECIES encryption."""
    try:
        with open(PUBKEYS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_pubkeys(data: dict):
    with open(PUBKEYS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_optins() -> dict:
    """Server-side opt-in records (fallback when contract call fails)."""
    try:
        with open(OPTINS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_optins(data: dict):
    with open(OPTINS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_sends() -> dict:
    """Per-address send usage tracking (server-side enforcement of free tier)."""
    try:
        with open(SENDS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_sends(data: dict):
    with open(SENDS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ════════════════════════════════════════════════════════════════════════════
# ON-CHAIN CALL — hasOptedIn / isSubscribed / freeSendsRemaining
# ════════════════════════════════════════════════════════════════════════════

def _eth_call(to: str, data_hex: str) -> str:
    """Low-level eth_call via JSON-RPC (no web3 dependency)."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data_hex}, "latest"],
    }).encode()
    req = urllib.request.Request(
        LCAI_RPC,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            return resp.get("result", "0x")
    except Exception as e:
        print(f"[eth_call] error: {e}")
        return "0x"


def _encode_has_opted_in(address: str) -> str:
    """ABI-encode hasOptedIn(address) call. Selector = keccak256('hasOptedIn(address)')[:4]"""
    selector = "0xc7a05f72"
    addr_padded = address[2:].lower().zfill(64)
    return selector + addr_padded

def _encode_is_subscribed(address: str) -> str:
    """ABI-encode isSubscribed(address) call. Selector = keccak256('isSubscribed(address)')[:4]"""
    selector = "0xb92ae87c"
    addr_padded = address[2:].lower().zfill(64)
    return selector + addr_padded

def _encode_free_sends_remaining(address: str) -> str:
    """ABI-encode freeSendsRemaining(address) call. Selector = keccak256('freeSendsRemaining(address)')[:4]"""
    selector = "0x1828adb5"
    addr_padded = address[2:].lower().zfill(64)
    return selector + addr_padded


def query_opted_in(address: str) -> dict:
    """Call OrcaMail contract to check opt-in status.  Returns {optedIn, preferences}."""
    if ORCAMAIL_CONTRACT == "0xTBD":
        return {"optedIn": False, "preferences": {}, "note": "contract_not_deployed"}

    result_hex = _eth_call(ORCAMAIL_CONTRACT, _encode_has_opted_in(address))
    opted_in = False
    if result_hex and result_hex != "0x":
        try:
            opted_in = int(result_hex, 16) != 0
        except ValueError:
            pass

    # Also check server-side fallback
    if not opted_in:
        with _data_lock:
            optins = load_optins()
            if address in optins:
                opted_in = True

    return {"optedIn": opted_in, "preferences": {}}


def query_subscription(address: str) -> dict:
    """Query contract for subscription status and free sends remaining."""
    is_subscribed = False
    free_sends = FREE_SENDS_LIMIT  # default if call fails

    # Check isSubscribed(address)
    try:
        result = _eth_call(ORCAMAIL_CONTRACT, _encode_is_subscribed(address))
        if result and result != "0x":
            is_subscribed = int(result, 16) != 0
    except Exception:
        pass

    # Check freeSendsRemaining(address)
    try:
        result = _eth_call(ORCAMAIL_CONTRACT, _encode_free_sends_remaining(address))
        if result and result != "0x" and len(result) > 2:
            free_sends = int(result, 16)
    except Exception:
        # Fall back to server-side tracking
        sends = load_sends()
        used = sends.get(address, {}).get("sends_used", 0)
        free_sends = max(0, FREE_SENDS_LIMIT - used)

    return {"is_subscribed": is_subscribed, "free_sends_remaining": free_sends}


# ════════════════════════════════════════════════════════════════════════════
# EMAIL NOTIFICATION
# ════════════════════════════════════════════════════════════════════════════

def send_notify_email(to_email: str, from_wallet: str):
    """Send a one-time new-message notification email."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print(f"[email] SMTP not configured — skipping notification to {to_email}")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "You have a new OrcaMail message"
        msg["From"]    = NOTIFY_FROM
        msg["To"]      = to_email

        text_body = (
            "You have a new OrcaMail message.\n\n"
            "Connect your wallet at https://orcamail.ai to read it.\n\n"
            "— The OrcaMail Team"
        )
        html_body = f"""\
<html>
  <body style="font-family:sans-serif;color:#1a1a2e;background:#f7f9fc;padding:32px;">
    <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;
                padding:32px;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <h2 style="color:#6c47ff;margin-top:0;">📬 New OrcaMail Message</h2>
      <p>You have a new encrypted message waiting in your OrcaMail inbox.</p>
      <p style="margin:24px 0;">
        <a href="https://orcamail.ai"
           style="background:#6c47ff;color:#fff;padding:12px 24px;
                  border-radius:8px;text-decoration:none;font-weight:bold;">
          Read Your Message
        </a>
      </p>
      <p style="font-size:13px;color:#888;">
        Connect your wallet at orcamail.ai to decrypt and read your message.
        Only you can read it — the content is end-to-end encrypted.
      </p>
    </div>
  </body>
</html>"""

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(NOTIFY_FROM, to_email, msg.as_string())

        print(f"[email] notification sent to {to_email}")

    except Exception as e:
        print(f"[email] failed to send to {to_email}: {e}")


# ════════════════════════════════════════════════════════════════════════════
# AIVM CLIENT — Lightchain Decentralized Inference
# Used by /api/aivm (POST) — server-side inference with dApp wallet.
# Requires env var: LIGHTCHAIN_PRIVATE_KEY
# ════════════════════════════════════════════════════════════════════════════

AIVM_GATEWAY  = "https://chat-api.mainnet.lightchain.ai"
AIVM_RELAY    = "wss://relay.mainnet.lightchain.ai/ws"
AIVM_RPC      = "https://rpc.mainnet.lightchain.ai"
AIVM_JOB_REG  = "0xfB15F90298e4CcD7106E76fFB5e520315cC42B0b"
AIVM_JOB_FEE  = 20_000_000_000_000_000   # 0.02 LCAI in wei
AIVM_CHAIN_ID = 9200

AIVM_ABI = [
    {
        "name": "createSession", "type": "function", "stateMutability": "payable",
        "inputs": [
            {"name": "paramsHash",     "type": "bytes32"},
            {"name": "worker",         "type": "address"},
            {"name": "encWorkerKey",   "type": "bytes"},
            {"name": "ephemeralPubKey","type": "bytes"},
            {"name": "initState",      "type": "bytes"},
            {"name": "expiry",         "type": "uint256"},
        ],
        "outputs": [{"name": "sessionId", "type": "uint256"}],
    },
    {
        "name": "submitJob", "type": "function", "stateMutability": "payable",
        "inputs": [
            {"name": "sessionId",  "type": "uint256"},
            {"name": "promptHash", "type": "bytes32"},
        ],
        "outputs": [{"name": "jobId", "type": "uint256"}],
    },
    {
        "anonymous": False, "name": "SessionCreated", "type": "event",
        "inputs": [
            {"indexed": True,  "name": "sessionId",      "type": "uint256"},
            {"indexed": True,  "name": "user",            "type": "address"},
            {"indexed": True,  "name": "paramsHash",      "type": "bytes32"},
            {"indexed": False, "name": "worker",          "type": "address"},
            {"indexed": False, "name": "encWorkerKey",    "type": "bytes"},
            {"indexed": False, "name": "ephemeralPubKey", "type": "bytes"},
        ],
    },
    {
        "anonymous": False, "name": "JobSubmitted", "type": "event",
        "inputs": [
            {"indexed": True,  "name": "jobId",     "type": "uint256"},
            {"indexed": True,  "name": "sessionId", "type": "uint256"},
            {"indexed": False, "name": "worker",    "type": "address"},
        ],
    },
    {
        "anonymous": False, "name": "JobCompleted", "type": "event",
        "inputs": [
            {"indexed": True,  "name": "jobId",         "type": "uint256"},
            {"indexed": True,  "name": "worker",         "type": "address"},
            {"indexed": False, "name": "responseHash",   "type": "bytes32"},
            {"indexed": False, "name": "ciphertextHash", "type": "bytes32"},
        ],
    },
]


def _decode_pubkey(s):
    """Accept hex (with/without 0x) or base64; return 65-byte uncompressed P-256 point."""
    if isinstance(s, (bytes, bytearray)):
        return bytes(s)
    s = s.strip()
    if s.startswith("0x") or s.startswith("0X"):
        b = bytes.fromhex(s[2:])
    elif len(s) == 130 and all(c in "0123456789abcdefABCDEF" for c in s):
        b = bytes.fromhex(s)
    else:
        b = base64.b64decode(s)
    if len(b) != 65:
        raise ValueError(f"pubkey decode: expected 65 bytes, got {len(b)}")
    return b


def _ecdh_wrap(session_key: bytes, peer_pub_bytes: bytes) -> bytes:
    """ECDH-wrap session_key for peer P-256 pubkey."""
    from cryptography.hazmat.primitives.asymmetric.ec import (
        generate_private_key, ECDH, EllipticCurvePublicNumbers, SECP256R1,
    )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.backends import default_backend

    x = int.from_bytes(peer_pub_bytes[1:33], "big")
    y = int.from_bytes(peer_pub_bytes[33:65], "big")
    peer_pub  = EllipticCurvePublicNumbers(x, y, SECP256R1()).public_key(default_backend())
    ephem_priv = generate_private_key(SECP256R1(), default_backend())
    shared     = ephem_priv.exchange(ECDH(), peer_pub)
    pub_nums   = ephem_priv.public_key().public_numbers()
    ephem_pub  = (b"\x04" +
                  pub_nums.x.to_bytes(32, "big") +
                  pub_nums.y.to_bytes(32, "big"))
    nonce  = secrets.token_bytes(12)
    ct_tag = AESGCM(shared).encrypt(nonce, session_key, None)
    return ephem_pub + nonce + ct_tag


def _aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """AES-256-GCM encrypt. Returns nonce(12) || ciphertext+tag."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def _aes_decrypt(key: bytes, blob: bytes) -> bytes:
    """AES-256-GCM decrypt nonce(12) || ciphertext+tag."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 28:
        raise ValueError("ciphertext too short")
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


class AIVMClient:
    """Server-side Lightchain AIVM inference using a dApp wallet private key."""

    def __init__(self, private_key: str):
        import requests as _req
        from web3 import Web3
        from eth_account import Account

        self._req      = _req
        self._w3       = Web3(Web3.HTTPProvider(AIVM_RPC))
        self._account  = Account.from_key(private_key)
        self._registry = self._w3.eth.contract(
            address=Web3.to_checksum_address(AIVM_JOB_REG),
            abi=AIVM_ABI,
        )
        self._jwt     = None
        self._jwt_exp = 0
        print(f"  [AIVM] wallet: {self._account.address}")

    def _get_jwt(self) -> str:
        from eth_account.messages import encode_defunct
        if self._jwt and time.time() < self._jwt_exp - 30:
            return self._jwt
        r = self._req.get(
            f"{AIVM_GATEWAY}/api/auth/challenge",
            params={"address": self._account.address}, timeout=15,
        )
        r.raise_for_status()
        message = r.json()["message"]
        sig = self._account.sign_message(encode_defunct(text=message))
        r2 = self._req.post(
            f"{AIVM_GATEWAY}/api/auth/verify",
            json={"message": message, "signature": "0x" + sig.signature.hex()},
            timeout=15,
        )
        r2.raise_for_status()
        v = r2.json()
        self._jwt = v["token"]
        exp_str = v["expiresAt"][:19].replace("T", " ")
        self._jwt_exp = time.mktime(time.strptime(exp_str, "%Y-%m-%d %H:%M:%S"))
        return self._jwt

    def _auth_headers(self):
        return {
            "Authorization": f"Bearer {self._get_jwt()}",
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }

    def run_inference(self, prompt: str, timeout_secs: int = 360) -> str:
        import websocket as _ws
        from web3 import Web3

        req = self._req
        print(f"  [AIVM] starting inference ({len(prompt)} chars)")

        # 1. Auth + pick model
        r = req.get(f"{AIVM_GATEWAY}/api/models", timeout=15)
        r.raise_for_status()
        models = r.json().get("models", [])
        model  = next((m for m in models if m["name"] == "llama3-8b"), models[0] if models else None)
        if not model:
            raise RuntimeError("No models available from AIVM gateway")
        model_id = model["id"]
        print(f"  [AIVM] model: {model['name']} id={model_id[:10]}...")

        # 2. Select worker
        r = req.post(
            f"{AIVM_GATEWAY}/api/sessions/select",
            json={"modelId": model_id},
            headers=self._auth_headers(), timeout=15,
        )
        r.raise_for_status()
        sel = r.json()
        print(f"  [AIVM] worker: {sel['worker']}")

        # 3. Session key + ECDH wrap
        session_key  = secrets.token_bytes(32)
        enc_worker   = _ecdh_wrap(session_key, _decode_pubkey(sel["workerEncryptionKey"]))
        enc_disputer = _ecdh_wrap(session_key, _decode_pubkey(sel["disputerEncryptionKey"]))

        # 4. Prepare (get dispatcher signature)
        r = req.post(
            f"{AIVM_GATEWAY}/api/sessions/prepare",
            json={
                "modelId":        model_id,
                "encWorkerKey":   base64.b64encode(enc_worker).decode(),
                "encDisputerKey": base64.b64encode(enc_disputer).decode(),
            },
            headers=self._auth_headers(), timeout=15,
        )
        r.raise_for_status()
        prep = r.json()

        # 5. createSession on-chain
        def _h(s): return s[2:] if isinstance(s, str) and s[:2].lower() == "0x" else s
        params_hash = bytes.fromhex(_h(model_id).zfill(64))
        sig_bytes   = bytes.fromhex(_h(prep["signature"]))
        gas_price   = self._w3.eth.gas_price
        nonce_val   = self._w3.eth.get_transaction_count(self._account.address)

        tx = self._registry.functions.createSession(
            params_hash,
            Web3.to_checksum_address(prep["worker"]),
            enc_worker,
            enc_disputer,
            sig_bytes,
            prep["expiry"],
        ).build_transaction({
            "from":     self._account.address,
            "nonce":    nonce_val,
            "gas":      1_000_000,
            "gasPrice": gas_price,
            "value":    0,
            "chainId":  AIVM_CHAIN_ID,
        })
        signed  = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  [AIVM] createSession tx: {tx_hash.hex()}")
        receipt1 = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        if receipt1.status != 1:
            raise RuntimeError("createSession reverted on-chain")

        session_id = None
        for log in receipt1.logs:
            try:
                evt = self._registry.events.SessionCreated().process_log(log)
                session_id = evt["args"]["sessionId"]
                break
            except Exception:
                pass
        if session_id is None:
            raise RuntimeError("SessionCreated event not found in receipt")
        print(f"  [AIVM] sessionId: {session_id}")

        # 6. Get relay token
        relay_token = None
        deadline = time.time() + 120
        while time.time() < deadline:
            r = req.get(
                f"{AIVM_GATEWAY}/api/sessions/{session_id}/token",
                headers=self._auth_headers(), timeout=10,
            )
            if r.status_code == 200:
                d = r.json()
                if d.get("token"):
                    relay_token = d["token"]
                    break
            time.sleep(1)
        if not relay_token:
            raise RuntimeError("Relay token not ready within 120s")

        # 7. Connect WebSocket relay
        chunks   = []
        ws_ready = threading.Event()
        ws_err   = [None]

        def _on_message(ws_obj, message):
            try:
                frame = json.loads(message)
                payload = frame.get("payload")
                if not payload:
                    return
                blob = base64.b64decode(payload)
                try:
                    pt = _aes_decrypt(session_key, blob)
                    chunks.append(pt.decode("utf-8", errors="replace"))
                except Exception:
                    pass
            except Exception:
                pass

        def _on_open(ws_obj):
            ws_ready.set()

        def _on_error(ws_obj, err):
            ws_err[0] = err
            ws_ready.set()

        ws = _ws.WebSocketApp(
            f"{AIVM_RELAY}?token={url_quote(relay_token)}",
            on_message=_on_message,
            on_open=_on_open,
            on_error=_on_error,
        )
        ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
        ws_thread.start()
        ws_ready.wait(timeout=15)
        if ws_err[0]:
            raise RuntimeError(f"WebSocket failed: {ws_err[0]}")
        print("  [AIVM] relay connected")

        # 8. Encrypt + upload prompt blob
        cipher = _aes_encrypt(session_key, prompt.encode("utf-8"))
        r = req.post(
            f"{AIVM_GATEWAY}/api/blobs",
            json={"data": base64.b64encode(cipher).decode()},
            headers=self._auth_headers(), timeout=15,
        )
        r.raise_for_status()
        blob_hashes = r.json().get("blobHashes", [])
        if not blob_hashes:
            raise RuntimeError("No blob hash returned from gateway")
        prompt_hash = bytes.fromhex(_h(blob_hashes[0]).zfill(64))

        # 9. submitJob (pay 0.02 LCAI)
        nonce_val2 = self._w3.eth.get_transaction_count(self._account.address)
        tx2 = self._registry.functions.submitJob(
            session_id,
            prompt_hash,
        ).build_transaction({
            "from":     self._account.address,
            "nonce":    nonce_val2,
            "gas":      500_000,
            "gasPrice": gas_price,
            "value":    AIVM_JOB_FEE,
            "chainId":  AIVM_CHAIN_ID,
        })
        signed2  = self._account.sign_transaction(tx2)
        tx_hash2 = self._w3.eth.send_raw_transaction(signed2.raw_transaction)
        print(f"  [AIVM] submitJob tx: {tx_hash2.hex()}")
        receipt2 = self._w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=90)
        if receipt2.status != 1:
            raise RuntimeError("submitJob reverted — check LCAI balance")

        job_id = None
        for log in receipt2.logs:
            try:
                evt = self._registry.events.JobSubmitted().process_log(log)
                job_id = evt["args"]["jobId"]
                break
            except Exception:
                pass
        if job_id is None:
            raise RuntimeError("JobSubmitted event not found in receipt")
        print(f"  [AIVM] jobId: {job_id}")

        # 10. Poll for JobCompleted or relay chunks
        job_completed_topic = "0x" + Web3.keccak(
            text="JobCompleted(uint256,address,bytes32,bytes32)"
        ).hex()
        job_id_topic = "0x" + hex(job_id)[2:].zfill(64)

        done     = False
        deadline = time.time() + timeout_secs
        while time.time() < deadline and not done:
            time.sleep(5)
            if chunks:
                print(f"  [AIVM] relay data arrived ({len(chunks)} chunks), returning early")
                done = True
                break
            try:
                head = self._w3.eth.block_number
                logs = self._w3.eth.get_logs({
                    "address":   Web3.to_checksum_address(AIVM_JOB_REG),
                    "fromBlock": receipt2.blockNumber,
                    "toBlock":   head,
                    "topics":    [job_completed_topic, job_id_topic],
                })
                if logs:
                    done = True
                    print(f"  [AIVM] JobCompleted on-chain!")
            except Exception as e:
                print(f"  [AIVM] log poll error (retrying): {e}")

        time.sleep(4)
        ws.close()

        result = "".join(chunks)
        if result:
            print(f"  [AIVM] inference done, {len(result)} chars")
            return result
        if not done:
            raise RuntimeError(f"Timeout after {timeout_secs}s waiting for result")
        return result or "AI completed the job but returned no text — please try again."


_aivm_client      = None
_aivm_client_lock = threading.Lock()


def get_aivm_client():
    global _aivm_client
    pk = os.environ.get("LIGHTCHAIN_PRIVATE_KEY", "").strip()
    if not pk:
        return None
    with _aivm_client_lock:
        if _aivm_client is None:
            try:
                _aivm_client = AIVMClient(pk)
            except Exception as e:
                print(f"  [AIVM] init failed: {e}")
                return None
    return _aivm_client


def run_aivm_inference(prompt: str) -> str:
    client = get_aivm_client()
    if client:
        try:
            return client.run_inference(prompt)
        except Exception as e:
            print(f"  [AIVM] inference failed: {e}")
            raise
    raise RuntimeError("AIVM unavailable — LIGHTCHAIN_PRIVATE_KEY not configured")


# ════════════════════════════════════════════════════════════════════════════
# HTTP HANDLER
# ════════════════════════════════════════════════════════════════════════════

class OrcaMailHandler(BaseHTTPRequestHandler):

    # ── Maintenance mode ─────────────────────────────────────────────────────

    def _serve_maintenance_page(self) -> bool:
        """Serve Coming Soon page if ~/MAINTENANCE_MODE exists. Returns True if served."""
        if not os.path.exists(MAINTENANCE_FLAG):
            return False
        html = _ORCAMAIL_MAINTENANCE_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)
        return True

    # ── Logging ─────────────────────────────────────────────────────────────

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    # ── Response helpers ─────────────────────────────────────────────────────

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status: int = 400):
        self._send_json({"error": message}, status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _get_query_param(self, params: dict, key: str) -> str:
        vals = params.get(key, [])
        return vals[0] if vals else ""

    # ── CORS pre-flight ──────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    # ── GET routing ──────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        # ── Frontend ──────────────────────────────────────────────
        if path == "" or path == "/":
            self._serve_frontend()
            return

        # ── GET /api/aivm/* — CORS proxy for OrcaFiles AI ────────────
        if path.startswith("/api/aivm/"):
            self._handle_aivm_proxy("GET", path)
            return

        # ── GET /api/health ───────────────────────────────────────
        if path == "/api/health":
            self._send_json({
                "status": "ok",
                "uptime": int(time.time()) - SERVER_START_TIME,
                "ts": int(time.time()),
            })
            return

        # ── GET /api/stats ────────────────────────────────────────
        if path == "/api/stats":
            self._handle_stats()
            return

        # ── GET /api/fee ──────────────────────────────────────────
        if path == "/api/fee":
            self._send_json({"fee_lcai": 1, "contract": ORCAMAIL_CONTRACT})
            return

        # ── GET /api/pubkey?address=0x... ─────────────────────────
        if path == "/api/pubkey":
            address = self._get_query_param(params, "address")
            self._handle_get_pubkey(address)
            return

        # ── GET /api/messages?address=0x... ──────────────────────
        if path == "/api/messages":
            address = self._get_query_param(params, "address")
            self._handle_inbox(address)
            return

        # ── GET /api/inbox/{address} (legacy path) ────────────────
        if path.startswith("/api/inbox/"):
            address = path[len("/api/inbox/"):]
            self._handle_inbox(address)
            return

        # ── GET /api/optin/status?address=0x... ──────────────────
        if path == "/api/optin/status":
            address = self._get_query_param(params, "address")
            self._handle_optin_check(address)
            return

        # ── GET /api/optin-check/{address} (legacy) ──────────────
        if path.startswith("/api/optin-check/"):
            address = path[len("/api/optin-check/"):]
            self._handle_optin_check(address)
            return

        # ── Static assets ─────────────────────────────────────────
        if path in ("/orcamail-logo.png",):
            self._serve_static(path[1:], "image/png")
            return
        if path == "/orca.gif":
            self._serve_static("orca.gif", "image/gif")
            return

        self._send_error("Not found", 404)

    # ── POST routing ─────────────────────────────────────────────────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        # ── POST /api/aivm/* — CORS proxy for OrcaFiles AI ───────────
        if path.startswith("/api/aivm/"):
            self._handle_aivm_proxy("POST", path)
            return

        # ── POST /api/send ────────────────────────────────────────
        if path == "/api/send":
            self._handle_send()
            return

        # ── POST /api/optin ───────────────────────────────────────
        if path == "/api/optin":
            self._handle_optin()
            return

        # ── POST /api/optout ──────────────────────────────────────
        if path == "/api/optout":
            self._handle_optout()
            return

        # ── POST /api/preferences ─────────────────────────────────
        if path == "/api/preferences":
            self._handle_preferences()
            return

        # ── POST /api/pubkey ──────────────────────────────────────
        if path == "/api/pubkey":
            self._handle_post_pubkey()
            return

        # ── POST /api/notify ──────────────────────────────────────
        if path == "/api/notify":
            self._handle_notify()
            return

        # ── POST /api/delete ─────────────────────────────────────
        if path == "/api/delete":
            self._handle_delete()
            return

        # ── POST /api/mark-read (v2) ──────────────────────────────
        if path == "/api/mark-read":
            self._handle_mark_read_v2()
            return

        # ── POST /api/read/{messageId} ────────────────────────────
        if path.startswith("/api/read/"):
            message_id = path[len("/api/read/"):]
            self._handle_mark_read(message_id)
            return

        # ── POST /api/aivm — server-side AIVM inference (OrcaMint format) ─
        if path == "/api/aivm":
            self._handle_aivm()
            return

        self._send_error("Not found", 404)

    # ════════════════════════════════════════════════════════════════════════
    # HANDLERS
    # ════════════════════════════════════════════════════════════════════════

    # ── AIVM CORS proxy (for OrcaFiles GitHub Pages) ─────────────────────────
    # Routes /api/aivm/<rest> → https://chat-api.mainnet.lightchain.ai/<rest>
    # Adds CORS headers so browser requests from orcafiles.ai work.

    AIVM_UPSTREAM = "https://chat-api.mainnet.lightchain.ai"

    def _handle_aivm_proxy(self, method, path):
        # Strip our prefix to get the upstream path
        upstream_path = path[len("/api/aivm"):]  # e.g. /api/models, /api/auth/challenge, etc.
        qs = urlparse(self.path).query
        upstream_url = self.AIVM_UPSTREAM + upstream_path + ("?" + qs if qs else "")

        # Forward Authorization header if present
        auth = self.headers.get("Authorization", "")
        fwd_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            fwd_headers["Authorization"] = auth

        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

        try:
            req = urllib.request.Request(upstream_url, data=body, headers=fwd_headers, method=method)
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_body = resp.read()
                status = resp.status
                ct = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            status = e.code
            ct = "application/json"
        except Exception as e:
            self._send_json({"error": str(e)}, 502)
            return

        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(resp_body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(resp_body)

    # ── Serve static files ───────────────────────────────────────────────────

    def _serve_static(self, filename: str, content_type: str):
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        if not os.path.exists(filepath):
            self._send_error("Not found", 404)
            return
        try:
            with open(filepath, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send_error(f"Failed to serve file: {e}", 500)

    # ── Serve frontend ───────────────────────────────────────────────────────

    def _serve_frontend(self):
        if not os.path.exists(FRONTEND_FILE):
            self._send_json({"status": "OrcaMail v2 server running"})
            return
        try:
            with open(FRONTEND_FILE, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send_error(f"Failed to serve frontend: {e}", 500)

    # ── GET /api/pubkey ──────────────────────────────────────────────────────

    def _handle_get_pubkey(self, address: str):
        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return
        address = normalize_address(address)
        with _data_lock:
            pubkeys = load_pubkeys()
        entry = pubkeys.get(address)
        if entry:
            self._send_json({"address": address, "pubkey": entry["pubkey"], "ts": entry.get("ts", 0)})
        else:
            self._send_json({"address": address, "pubkey": None})

    # ── POST /api/pubkey ─────────────────────────────────────────────────────

    def _handle_post_pubkey(self):
        body    = self._read_body()
        address = body.get("address", "")
        pubkey  = body.get("pubkey", "")

        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return
        if not pubkey or not isinstance(pubkey, str) or len(pubkey) < 10:
            self._send_error("pubkey is required (compressed secp256k1 hex)")
            return

        address = normalize_address(address)

        with _data_lock:
            pubkeys = load_pubkeys()
            pubkeys[address] = {
                "pubkey": pubkey,
                "ts": int(time.time()),
            }
            save_pubkeys(pubkeys)

        print(f"[pubkey] registered for {address}")
        self._send_json({"ok": True, "address": address}, 201)

    # ── POST /api/optin ──────────────────────────────────────────────────────

    def _handle_optin(self):
        body    = self._read_body()
        address = body.get("address", "")
        prefs   = body.get("preferences", {})
        pubkey  = body.get("pubkey", "")   # secp256k1 public key for ECIES encryption

        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return

        address = normalize_address(address)

        with _data_lock:
            # Save opt-in record
            optins = load_optins()
            optins[address] = {
                "preferences": prefs,
                "ts": int(time.time()),
            }
            save_optins(optins)

            # Save pubkey so senders can encrypt messages to this address
            if pubkey and isinstance(pubkey, str) and len(pubkey) >= 10:
                pubkeys = load_pubkeys()
                pubkeys[address] = {
                    "pubkey": pubkey,
                    "ts": int(time.time()),
                }
                save_pubkeys(pubkeys)
                print(f"[optin] pubkey saved for {address}: {pubkey[:20]}...")

            stats = load_stats()
            # Count as opted in if not already counted
            cache = stats.setdefault("opted_in_cache", {})
            if address not in cache or not cache[address].get("optedIn"):
                stats["total_opted_in"] = stats.get("total_opted_in", 0) + 1
                cache[address] = {"optedIn": True, "ts": int(time.time())}
                save_stats(stats)

        print(f"[optin] {address} opted in with prefs {prefs}")
        self._send_json({"ok": True, "address": address}, 201)

    # ── POST /api/optout ─────────────────────────────────────────────────────

    def _handle_optout(self):
        body    = self._read_body()
        address = body.get("address", "")

        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return

        address = normalize_address(address)

        with _data_lock:
            # Remove pubkey so new senders can't encrypt to this address
            pubkeys = load_pubkeys()
            pubkeys.pop(address, None)
            save_pubkeys(pubkeys)

            # Remove server-side opt-in record
            optins = load_optins()
            optins.pop(address, None)
            save_optins(optins)

            # Update stats cache
            stats = load_stats()
            cache = stats.setdefault("opted_in_cache", {})
            if address in cache:
                cache[address]["optedIn"] = False
                cache[address]["ts"] = int(time.time())
            stats["total_opted_in"] = max(0, stats.get("total_opted_in", 0) - 1)
            save_stats(stats)

        print(f"[optout] {address} opted out")
        self._send_json({"ok": True, "address": address})

    # ── POST /api/preferences ────────────────────────────────────────────────

    def _handle_preferences(self):
        body    = self._read_body()
        address = body.get("address", "")
        prefs   = body.get("preferences", {})

        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return

        address = normalize_address(address)

        with _data_lock:
            optins = load_optins()
            if address not in optins:
                optins[address] = {}
            optins[address]["preferences"] = prefs
            optins[address]["prefs_updated"] = int(time.time())
            save_optins(optins)

        self._send_json({"ok": True})

    # ── POST /api/notify ─────────────────────────────────────────────────────

    def _handle_notify(self):
        body  = self._read_body()
        to    = body.get("to", "")
        email = body.get("email", "")
        frm   = body.get("from", "unknown")

        if not email or "@" not in email:
            self._send_error("Valid email required")
            return

        threading.Thread(
            target=send_notify_email,
            args=(email, frm),
            daemon=True,
        ).start()

        self._send_json({"ok": True, "queued": True})

    # ── POST /api/send ───────────────────────────────────────────────────────

    def _handle_send(self):
        body = self._read_body()

        # Accept v2 field names (encrypted_body, subject, preview) and v1 fallbacks
        from_addr         = body.get("from", "")
        to_addr           = body.get("to", "")
        encrypted_content = body.get("encrypted_body") or body.get("encryptedContent", "")
        subject           = body.get("subject", "(no subject)")
        preview           = body.get("preview", "")
        message_type      = body.get("messageType", "text")

        if not is_valid_address(from_addr):
            self._send_error("Invalid 'from' Ethereum address")
            return
        if not is_valid_address(to_addr):
            self._send_error("Invalid 'to' Ethereum address")
            return
        if not encrypted_content:
            self._send_error("encrypted_body is required")
            return

        from_addr = normalize_address(from_addr)
        to_addr   = normalize_address(to_addr)

        # Check send allowance (subscription or free tier)
        sub = query_subscription(from_addr)
        if not sub["is_subscribed"]:
            # Check server-side send count as authoritative fallback
            with _data_lock:
                sends = load_sends()
                used  = sends.get(from_addr, {}).get("sends_used", 0)
            free_remaining = max(0, FREE_SENDS_LIMIT - used)
            if free_remaining <= 0 and sub["free_sends_remaining"] <= 0:
                self._send_error("No sends remaining. Please subscribe to continue.", 402)
                return

        message_id = str(uuid.uuid4())
        timestamp  = int(time.time())

        message_obj = {
            "messageId":        message_id,
            "id":               message_id,
            "from":             from_addr,
            "to":               to_addr,
            "encrypted_body":   encrypted_content,
            "encryptedContent": encrypted_content,  # v1 compat
            "subject":          subject,
            "preview":          preview,
            "messageType":      message_type,
            "timestamp":        timestamp,
            "delivered":        False,
            "read":             False,
        }

        with _data_lock:
            messages = load_messages()
            if to_addr not in messages:
                messages[to_addr] = []
            messages[to_addr].append(message_obj)
            save_messages(messages)

            stats = load_stats()
            stats["total_messages"] = stats.get("total_messages", 0) + 1
            save_stats(stats)

            # Track server-side send count for free tier enforcement
            if not sub["is_subscribed"]:
                sends = load_sends()
                if from_addr not in sends:
                    sends[from_addr] = {"sends_used": 0}
                sends[from_addr]["sends_used"] = sends[from_addr].get("sends_used", 0) + 1
                save_sends(sends)

        self._send_json({"messageId": message_id, "ok": True}, 201)

    # ── GET /api/messages (also /api/inbox/{address}) ────────────────────────

    def _handle_inbox(self, address: str):
        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return

        address = normalize_address(address)

        with _data_lock:
            messages = load_messages()
            inbox    = messages.get(address, [])

            updated = False
            for msg in inbox:
                if not msg.get("delivered"):
                    msg["delivered"] = True
                    updated = True
            if updated:
                save_messages(messages)

        result = [
            {
                # v2 field names (used by orcamail-v2.html)
                "id":               m.get("id") or m.get("messageId", ""),
                "from":             m["from"],
                "encrypted_body":   m.get("encrypted_body") or m.get("encryptedContent", ""),
                "subject":          m.get("subject", "(no subject)"),
                "preview":          m.get("preview", ""),
                "timestamp":        m["timestamp"],
                "read":             m.get("read", False),
                # v1 compat fields
                "messageId":        m.get("messageId") or m.get("id", ""),
                "encryptedContent": m.get("encryptedContent") or m.get("encrypted_body", ""),
            }
            for m in inbox
        ]

        self._send_json({"messages": result, "count": len(result)})

    # ── POST /api/delete ─────────────────────────────────────────────────────

    def _handle_delete(self):
        body       = self._read_body()
        address    = body.get("address", "")
        message_id = body.get("messageId", "") or body.get("id", "")

        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return
        if not message_id:
            self._send_error("messageId is required")
            return

        address = normalize_address(address)

        with _data_lock:
            messages = load_messages()
            inbox    = messages.get(address, [])
            before   = len(inbox)
            # Match by either id or messageId (v2 and v1 compat)
            messages[address] = [
                m for m in inbox
                if m.get("messageId") != message_id and m.get("id") != message_id
            ]
            deleted  = len(messages[address]) < before
            if deleted:
                save_messages(messages)
                stats = load_stats()
                stats["total_messages"] = max(0, stats.get("total_messages", 0) - 1)
                save_stats(stats)

        if not deleted:
            self._send_error("Message not found", 404)
            return

        self._send_json({"ok": True})

    # ── POST /api/mark-read (v2 — no signature required) ────────────────────

    def _handle_mark_read_v2(self):
        body       = self._read_body()
        address    = body.get("address", "")
        message_id = body.get("messageId", "")

        if not is_valid_address(address):
            self._send_error("Invalid address")
            return
        if not message_id:
            self._send_error("messageId is required")
            return

        address = normalize_address(address)
        with _data_lock:
            messages = load_messages()
            inbox    = messages.get(address, [])
            for msg in inbox:
                if msg.get("messageId") == message_id or msg.get("id") == message_id:
                    msg["read"] = True
                    break
            save_messages(messages)
        self._send_json({"ok": True})

    # ── POST /api/read/{messageId} ───────────────────────────────────────────

    def _handle_mark_read(self, message_id: str):
        if not message_id:
            self._send_error("messageId is required")
            return

        body      = self._read_body()
        address   = body.get("address", "")
        signature = body.get("signature", "")

        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return
        if not signature:
            self._send_error("signature is required")
            return

        address = normalize_address(address)

        with _data_lock:
            messages = load_messages()
            inbox    = messages.get(address, [])
            found    = False
            for msg in inbox:
                if msg["messageId"] == message_id:
                    msg["read"] = True
                    found = True
                    break
            if not found:
                for addr_key, msgs in messages.items():
                    for msg in msgs:
                        if msg["messageId"] == message_id and msg.get("to") == address:
                            msg["read"] = True
                            found = True
                            break
                    if found:
                        break
            if found:
                save_messages(messages)

        if not found:
            self._send_error("Message not found or access denied", 404)
            return

        self._send_json({"ok": True})

    # ── GET /api/optin/status ────────────────────────────────────────────────

    def _handle_optin_check(self, address: str):
        if not is_valid_address(address):
            self._send_error("Invalid Ethereum address")
            return

        address = normalize_address(address)

        with _data_lock:
            stats  = load_stats()
            cache  = stats.setdefault("opted_in_cache", {})
            cached = cache.get(address)
            now    = int(time.time())

            if cached and (now - cached.get("ts", 0)) < 300:
                optins = load_optins()
                prefs  = optins.get(address, {}).get("preferences", {})
                sub    = query_subscription(address)
                self._send_json({
                    "optedIn":              cached["optedIn"],
                    "opted_in":             cached["optedIn"],
                    "preferences":          prefs,
                    "is_subscribed":        sub["is_subscribed"],
                    "free_sends_remaining": sub["free_sends_remaining"],
                    "cached":               True,
                })
                return

        result = query_opted_in(address)

        with _data_lock:
            stats = load_stats()
            cache = stats.setdefault("opted_in_cache", {})
            cache[address] = {
                "optedIn": result["optedIn"],
                "preferences": result.get("preferences", {}),
                "ts": now,
            }
            if result["optedIn"]:
                existing_opted_in = sum(1 for v in cache.values() if v.get("optedIn"))
                stats["total_opted_in"] = max(stats.get("total_opted_in", 0), existing_opted_in)
            save_stats(stats)

        with _data_lock:
            optins = load_optins()
        prefs = optins.get(address, {}).get("preferences", result.get("preferences", {}))
        sub   = query_subscription(address)

        self._send_json({
            "optedIn":              result["optedIn"],
            "opted_in":             result["optedIn"],
            "preferences":          prefs,
            "is_subscribed":        sub["is_subscribed"],
            "free_sends_remaining": sub["free_sends_remaining"],
        })

    # ── GET /api/stats ───────────────────────────────────────────────────────

    def _handle_stats(self):
        with _data_lock:
            stats    = load_stats()
            messages = load_messages()

        total_messages = stats.get("total_messages", 0)
        total_opted_in = stats.get("total_opted_in", 0)

        unique_senders    = set()
        unique_recipients = set()
        for addr, msgs in messages.items():
            unique_recipients.add(addr)
            for m in msgs:
                unique_senders.add(m.get("from", ""))

        self._send_json({
            "totalMessages":    total_messages,
            "totalOptedIn":     total_opted_in,
            "uniqueSenders":    len(unique_senders),
            "uniqueRecipients": len(unique_recipients),
            "contractAddress":  ORCAMAIL_CONTRACT,
            "network":          "Lightchain Mainnet",
        })


    # ── POST /api/aivm — server-side inference (OrcaMint / OrcaFiles simple format) ─

    def _handle_aivm(self):
        body     = self._read_body()
        messages = body.get("messages", [])
        if not messages:
            self._send_error("messages array is required", 400)
            return

        # Extract the user prompt from messages array (OpenAI format)
        prompt = ""
        for m in messages:
            if m.get("role") == "user":
                prompt = m.get("content", "")
                break
        if not prompt:
            self._send_error("No user message found in messages array", 400)
            return

        try:
            result = run_aivm_inference(prompt)
            self._send_json({
                "choices": [{"message": {"role": "assistant", "content": result}}],
                "model":   "lightchain-aivm",
            })
        except Exception as e:
            print(f"[aivm] error: {e}")
            self._send_error(f"AIVM inference failed: {e}", 503)


# ════════════════��═════════════════════════════════════════════════���═════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    for fpath in (DATA_FILE, STATS_FILE, PUBKEYS_FILE, OPTINS_FILE):
        if not os.path.exists(fpath):
            with open(fpath, "w") as f:
                json.dump({}, f)

    server = HTTPServer(("0.0.0.0", PORT), OrcaMailHandler)
    print(f"OrcaMail server v1.1.0 running on http://0.0.0.0:{PORT}")
    print(f"  Contract : {ORCAMAIL_CONTRACT}")
    print(f"  RPC      : {LCAI_RPC}")
    print(f"  Data     : {DATA_FILE}")
    print(f"  Pubkeys  : {PUBKEYS_FILE}")
    print(f"  SMTP     : {SMTP_HOST or '(not configured)'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down OrcaMail server.")
        server.server_close()


if __name__ == "__main__":
    main()
