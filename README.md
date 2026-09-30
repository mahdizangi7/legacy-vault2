# LegacyVault × GenLayer

A decentralized dead-man's-switch inheritance vault powered by **GenLayer validator consensus**.

LegacyVault enables users to securely lock GEN assets in an inheritance vault. If the owner stops checking in, the vault can enter a release process where beneficiaries receive the locked assets. Before release, GenLayer validators independently verify a public attestation source to determine whether the owner's attestation is still valid.

The project demonstrates how GenLayer can combine:

* Smart contract state management
* Validator-based external data verification
* Public web attestations
* Consensus-driven execution
* Time-based decentralized inheritance logic

---

## Overview

Traditional inheritance systems rely on centralized intermediaries, legal processes, or trusted custodians.

LegacyVault introduces a decentralized alternative:

1. A vault owner creates an inheritance vault.
2. The owner deposits GEN tokens.
3. Beneficiaries and optional guardians are configured.
4. The owner periodically checks in to prove activity.
5. If the owner stops checking in, the release process begins.
6. GenLayer validators verify a public attestation page.
7. Depending on the attestation result:

   * Release can be blocked if the attestation is still valid.
   * Release can continue if the attestation is missing.

---

# Features

## Dead Man's Switch

Each vault contains:

* Owner address
* Beneficiary list
* Distribution percentages
* Check-in interval
* Grace period
* Contest period

The owner can periodically call:

```
check_in(vault_id)
```

to reset the inactivity timer.

---

## GEN Asset Management

Vault owners can:

* Create vaults with an initial deposit
* Add additional deposits
* Withdraw unlocked funds

Supported actions:

```
create_vault()
deposit()
owner_withdraw_unlocked()
```

---

## Beneficiary Distribution

Vaults support multiple beneficiaries.

Example:

```
Alice: 70%
Bob:   30%
```

The contract stores beneficiary allocation using basis points:

```
10000 bps = 100%
```

After successful release, beneficiaries can claim their assigned share.

---

# GenLayer Attestation System

The key feature of LegacyVault is validator-powered attestation verification.

Each vault can contain:

```
attestation_url
attestation_token
```

Example:

```
URL:
https://attestation-flax.vercel.app

Token:
legacyvault-2-de71474ef2f8
```

When release is requested, GenLayer validators independently inspect the public URL.

The validator checks:

```
Does the expected token exist?
```

---

## Attestation Outcomes

### Token Found

Validator result:

```json
{
  "found": true
}
```

Contract behavior:

```
ATTESTATION_STILL_LIVE
```

Release is blocked because the attestation is still active.

---

### Token Missing

Validator result:

```json
{
  "found": false
}
```

Contract behavior:

```
pending_release
```

The vault enters the release flow.

---

# Architecture

```
                 User Wallet
                     |
                     |
                     v
              LegacyVault Contract
                     |
        -----------------------------
        |                           |
        v                           v
 GenLayer Validators          Vault State
        |
        |
        v
 Public Attestation URL
        |
        |
        v
 Token Verification
```

---

# Smart Contract

Built for:

```
GenLayer Studionet
```

Contract responsibilities:

* Vault creation
* GEN deposits
* Ownership tracking
* Check-in management
* Release workflow
* Guardian voting
* Validator attestation verification
* Beneficiary claims

---

# Contract Address

```
<ADD_CONTRACT_ADDRESS_HERE>
```

Network:

```
GenLayer Studionet
```

Chain ID:

```
61999
```

RPC:

```
https://studio.genlayer.com/api
```

---

# Frontend

The frontend is a lightweight HTML application connected directly to GenLayer.

Features:

* Wallet connection
* Vault listing
* Vault details
* Create vault interface
* Deposit GEN
* Check-in
* Release management
* Claiming
* Attestation status display

Tech stack:

* HTML
* JavaScript Modules
* genlayer-js
* MetaMask

---

# Live Demo

Frontend:

```
<ADD_VERCEL_URL>
```

Attestation Demo:

```
https://attestation-flax.vercel.app
```

---

# Testing Evidence

## Vault Creation

Successfully created vaults with:

* GEN deposits
* Beneficiaries
* Guardians
* Attestation URLs

---

## Attestation Live Test

When the public token existed:

Validator output:

```json
{
  "found": true
}
```

Execution:

```
[EXPECTED] ATTESTATION_STILL_LIVE
```

Result:

Release prevented.

---

## Missing Attestation Test

After removing the token from the public page:

Validator output:

```json
{
  "found": false
}
```

Vault state:

```json
{
  "status": "pending_release",
  "pending_reason": "attestation_absent"
}
```

Result:

Release process started.

---

# Guardian System

Vaults can optionally configure guardians.

Guardians can participate in emergency release decisions.

Parameters:

```
guardian_threshold
guardian_votes
guardians
```

---

# Local Development

Clone repository:

```bash
git clone <repository-url>
```

Enter frontend:

```bash
cd frontend
```

Run locally:

```bash
npx serve .
```

Open:

```
http://localhost:3000
```

---

# Deployment

The frontend can be deployed using Vercel.

Install:

```bash
npm install -g vercel
```

Deploy:

```bash
vercel --prod
```

---

# Security Considerations

LegacyVault uses several safety mechanisms:

* Validator consensus instead of centralized APIs
* Public attestation verification
* Time-based release protection
* Guardian emergency controls
* Beneficiary percentage validation
* Minimum timing constraints

---

# Future Improvements

Possible extensions:

* Multiple independent attestations
* Social recovery mechanisms
* NFT inheritance certificates
* More flexible guardian policies
* Multi-chain asset support
* Improved attestation dashboards

---

# Built With

* GenLayer
* genlayer-js
* Vercel
* MetaMask
* JavaScript
* HTML/CSS

---

# License

MIT License
