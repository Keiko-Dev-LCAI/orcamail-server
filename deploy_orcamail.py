#!/usr/bin/env python3
"""
OrcaMail deployment script — Lightchain mainnet (chain ID 9200)
"""

import sys
import json
import time

# Ensure installed packages are on path
sys.path.insert(0, "/tmp/pylibs")

from web3 import Web3
from solcx import compile_source, install_solc, get_installed_solc_versions

# ─── Config ───────────────────────────────────────────────────────────────────
RPC_URL     = "https://rpc.mainnet.lightchain.ai"
CHAIN_ID    = 9200
PRIVATE_KEY = "0xdf7ed1419befce2cc6aa5dc1f14f947197b01be72e4d78ccbece58bae34f4554"
DEPLOYER    = "0x729fea1d8cA343F26C4cc743a4e1898d65cE6A76"

SEND_FEE    = Web3.to_wei(1,   "ether")   # 1 LCAI
BULK_FEE    = Web3.to_wei(0.1, "ether")   # 0.1 LCAI per recipient

SOL_FILE    = "/sessions/fervent-determined-mendel/mnt/Desktop/OrcaMail.sol"
ABI_OUT     = "/sessions/fervent-determined-mendel/mnt/Desktop/orcamail-abi.json"
ADDR_OUT    = "/sessions/fervent-determined-mendel/mnt/Desktop/orcamail-address.txt"

# ─── Connect ──────────────────────────────────────────────────────────────────
print(f"[1/6] Connecting to {RPC_URL} …")
w3 = Web3(Web3.HTTPProvider(RPC_URL))
assert w3.is_connected(), "Cannot reach RPC endpoint"
print(f"      Chain ID : {w3.eth.chain_id}")
bal = w3.eth.get_balance(DEPLOYER)
print(f"      Deployer : {DEPLOYER}")
print(f"      Balance  : {Web3.from_wei(bal, 'ether'):.4f} LCAI")

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
print(f"\n[5/6] Deploying OrcaMail to Lightchain mainnet …")
OrcaMail = w3.eth.contract(abi=abi, bytecode=bytecode)

nonce = w3.eth.get_transaction_count(DEPLOYER)
gas_price = w3.eth.gas_price
print(f"      Nonce      : {nonce}")
print(f"      Gas price  : {Web3.from_wei(gas_price, 'gwei'):.4f} Gwei")

constructor_tx = OrcaMail.constructor(SEND_FEE, BULK_FEE).build_transaction({
    "from":     DEPLOYER,
    "nonce":    nonce,
    "chainId":  CHAIN_ID,
    "gasPrice": gas_price,
})

# Estimate gas
estimated_gas = w3.eth.estimate_gas(constructor_tx)
constructor_tx["gas"] = int(estimated_gas * 1.2)   # 20% buffer
print(f"      Est. gas   : {estimated_gas:,}  (sending {constructor_tx['gas']:,})")

signed_tx = w3.eth.account.sign_transaction(constructor_tx, PRIVATE_KEY)
tx_hash   = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print(f"      TX hash    : {tx_hash.hex()}")
print("      Waiting for receipt …")

receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
contract_address = receipt["contractAddress"]

print(f"\n[6/6] ✅  Contract deployed!")
print(f"      Address    : {contract_address}")
print(f"      Block      : {receipt['blockNumber']}")
print(f"      Gas used   : {receipt['gasUsed']:,}")
print(f"      Status     : {'SUCCESS' if receipt['status'] == 1 else 'FAILED'}")

if receipt["status"] != 1:
    print("ERROR: transaction reverted — deployment failed.")
    sys.exit(1)

# ─── Save address ─────────────────────────────────────────────────────────────
with open(ADDR_OUT, "w") as f:
    f.write(f"OrcaMail Contract Address: {contract_address}\n")
    f.write(f"Chain ID: {CHAIN_ID}\n")
    f.write(f"TX Hash: {tx_hash.hex()}\n")
    f.write(f"Block: {receipt['blockNumber']}\n")
    f.write(f"Deployer: {DEPLOYER}\n")
print(f"\n      Address saved to {ADDR_OUT}")
print(f"\n{'='*60}")
print(f"  OrcaMail deployed at: {contract_address}")
print(f"{'='*60}\n")
