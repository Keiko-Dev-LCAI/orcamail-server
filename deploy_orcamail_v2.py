#!/usr/bin/env python3
"""
OrcaMail v2 deployment script — Lightchain mainnet (chain ID 9200)

Pricing model: 5 free sends, then $0.50/month subscription.
minSubPrice initial value: 100 LCAI (owner can adjust via setMinSubPrice).

At current LCAI price (~$0.004):  $0.50 = ~125 LCAI  → set to 100 LCAI floor
If LCAI rises to $0.01:            $0.50 = ~50 LCAI   → call setMinSubPrice(50 ether)
If LCAI rises to $0.10:            $0.50 = ~5 LCAI    → call setMinSubPrice(5 ether)
"""

import sys
import json
import time

sys.path.insert(0, "/tmp/pylibs")

from web3 import Web3
from solcx import compile_source, install_solc, get_installed_solc_versions

# ─── Config ───────────────────────────────────────────────────────────────────
RPC_URL     = "https://rpc.mainnet.lightchain.ai"
CHAIN_ID    = 9200
PRIVATE_KEY = "0xdf7ed1419befce2cc6aa5dc1f14f947197b01be72e4d78ccbece58bae34f4554"
DEPLOYER    = "0x729fea1d8cA343F26C4cc743a4e1898d65cE6A76"

# Initial subscription price floor: 100 LCAI
MIN_SUB_PRICE = Web3.to_wei(100, "ether")

SOL_FILE  = "/home/keiko/Desktop/OrcaMail_v2.sol"
ABI_OUT   = "/home/keiko/Desktop/orcamail-abi-v2.json"
ADDR_OUT  = "/home/keiko/Desktop/orcamail-address-v2.txt"

# ─── Connect ──────────────────────────────────────────────────────────────────
print(f"[1/6] Connecting to {RPC_URL} …")
w3 = Web3(Web3.HTTPProvider(RPC_URL))
assert w3.is_connected(), "Cannot reach RPC endpoint"
print(f"      Chain ID : {w3.eth.chain_id}")
bal = w3.eth.get_balance(DEPLOYER)
print(f"      Deployer : {DEPLOYER}")
print(f"      Balance  : {Web3.from_wei(bal, 'ether'):.4f} LCAI")

if Web3.from_wei(bal, 'ether') < 0.1:
    print("WARNING: Deployer balance is low — may not have enough for gas!")

# ─── Install solc ─────────────────────────────────────────────────────────────
SOLC_VERSION = "0.8.20"
print(f"\n[2/6] Ensuring solc {SOLC_VERSION} is installed …")
installed = [str(v) for v in get_installed_solc_versions()]
if SOLC_VERSION not in installed:
    print("      Installing — this may take a minute …")
    install_solc(SOLC_VERSION)
print(f"      solc {SOLC_VERSION} ready")

# ─── Compile ──────────────────────────────────────────────────────────────────
print(f"\n[3/6] Compiling {SOL_FILE} …")
with open(SOL_FILE) as f:
    source = f.read()

compiled = compile_source(
    source,
    output_values=["abi", "bin"],
    solc_version=SOLC_VERSION,
    optimize=True,
    optimize_runs=200,
)

contract_id = "<stdin>:OrcaMail"
contract_interface = compiled[contract_id]
abi      = contract_interface["abi"]
bytecode = contract_interface["bin"]
print(f"      Bytecode size : {len(bytecode)//2:,} bytes")

# ─── Save ABI ─────────────────────────────────────────────────────────────────
print(f"\n[4/6] Writing ABI to {ABI_OUT} …")
with open(ABI_OUT, "w") as f:
    json.dump(abi, f, indent=2)
print("      ABI saved.")

# ─── Build & send deployment tx ───────────────────────────────────────────────
print(f"\n[5/6] Deploying OrcaMail v2 to Lightchain mainnet …")
print(f"      minSubPrice : {Web3.from_wei(MIN_SUB_PRICE, 'ether')} LCAI")

OrcaMail = w3.eth.contract(abi=abi, bytecode=bytecode)

nonce     = w3.eth.get_transaction_count(DEPLOYER)
gas_price = w3.eth.gas_price
print(f"      Nonce      : {nonce}")
print(f"      Gas price  : {Web3.from_wei(gas_price, 'gwei'):.4f} Gwei")

constructor_tx = OrcaMail.constructor(MIN_SUB_PRICE).build_transaction({
    "from":     DEPLOYER,
    "nonce":    nonce,
    "chainId":  CHAIN_ID,
    "gasPrice": gas_price,
})

estimated_gas = w3.eth.estimate_gas(constructor_tx)
constructor_tx["gas"] = int(estimated_gas * 1.2)
print(f"      Est. gas   : {estimated_gas:,}  (sending {constructor_tx['gas']:,})")

signed_tx = w3.eth.account.sign_transaction(constructor_tx, PRIVATE_KEY)
tx_hash   = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print(f"      TX hash    : {tx_hash.hex()}")
print("      Waiting for receipt …")

receipt          = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
contract_address = receipt["contractAddress"]

print(f"\n[6/6] ✅  OrcaMail v2 deployed!")
print(f"      Address    : {contract_address}")
print(f"      Block      : {receipt['blockNumber']}")
print(f"      Gas used   : {receipt['gasUsed']:,}")
print(f"      Status     : {'SUCCESS' if receipt['status'] == 1 else 'FAILED'}")

if receipt["status"] != 1:
    print("ERROR: transaction reverted — deployment failed.")
    sys.exit(1)

# ─── Save address ─────────────────────────────────────────────────────────────
with open(ADDR_OUT, "w") as f:
    f.write(f"OrcaMail v2 Contract Address: {contract_address}\n")
    f.write(f"Chain ID: {CHAIN_ID}\n")
    f.write(f"TX Hash: {tx_hash.hex()}\n")
    f.write(f"Block: {receipt['blockNumber']}\n")
    f.write(f"Deployer: {DEPLOYER}\n")
    f.write(f"minSubPrice: 100 LCAI\n")

print(f"\n      Address saved to {ADDR_OUT}")
print(f"\n{'='*60}")
print(f"  OrcaMail v2 deployed at: {contract_address}")
print(f"{'='*60}")
print(f"\nNEXT STEPS:")
print(f"  1. Update ORCAMAIL_CONTRACT in orcamail.html to: {contract_address}")
print(f"  2. Update ORCAMAIL_CONTRACT in orcamail-server.py to: {contract_address}")
print(f"  3. Restart orcamail-server: sudo systemctl restart orcamail-server")
print(f"  4. If LCAI price changed, call setMinSubPrice(new_price_in_wei) from owner wallet")
print(f"     Current floor: 100 LCAI (~$0.40 at $0.004/LCAI)")
print(f"\n")
