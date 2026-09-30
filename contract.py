# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

# LegacyVault - a dead-man's-switch digital inheritance vault.
#
# Owner escrows GEN and names beneficiaries (basis-point shares) plus
# optional guardians. The owner must "check in" periodically. If the
# deadline passes, anyone can request release - but first GenLayer's
# validators independently visit a public page the owner nominated and
# agree on whether a unique attestation token is still present there.
#
# v2 changelog (security review fixes):
#   1. FAIL-CLOSED ATTESTATION. v1 caught fetch/render exceptions and
#      silently treated them as "token absent", which is exactly the
#      value that unblocks release - a network hiccup could release
#      funds out from under a perfectly alive owner. v2 never converts
#      a fetch error into a release-enabling result: a leader-side
#      error aborts the whole call (nothing is saved), and a
#      validator-side error counts as disagreement, never silent
#      agreement. Either way, an outage can only ever fail to release
#      funds, never accidentally cause one.
#   2. AUTHORITATIVE ATTESTATION SOURCE. All contract state, including
#      attestation_token, is public - anyone can read it from
#      get_vault. That means a bare "does this arbitrary URL contain
#      this string" check isn't actually bound to the owner in any
#      strong sense: whoever can write to that URL (now or after a
#      domain lapses, a host is compromised, or a shared page is
#      edited by someone else) can produce the same signal, forever,
#      with no ongoing owner involvement. A real fix is a private-key
#      signature the owner alone can produce; that wasn't implemented
#      here because this pinned GenVM runtime's Python sandbox doesn't
#      have confirmed access to an ECDSA/secp256k1 library, and
#      shipping unverified crypto is worse than not shipping it. As an
#      achievable interim fix, attestation_url is now restricted to a
#      single allowlisted, identity-bound prefix
#      (raw.githubusercontent.com) - the same pattern this project
#      already uses for PredictionMarket's CoinGecko allowlist -
#      raising the bar from "anyone who can write to any URL you name"
#      to "someone who compromises a specific, pre-named GitHub
#      account". See README for the full reasoning and the signature-
#      based upgrade path.
#   3. MALFORMED / DUPLICATE BENEFICIARIES REJECTED. v1 didn't
#      validate beneficiary address format at all, and didn't reject
#      duplicates. A duplicate address silently broke claim() (it
#      `break`s on the first match, permanently stranding every later
#      duplicate entry's share). v2 validates every beneficiary and
#      guardian address is a well-formed 0x... hex address and rejects
#      duplicate beneficiary addresses outright.
#
# This is still the GenLayer-specific piece worth having: validators
# reach consensus on a fact about the live outside world (does this
# specific, pre-committed URL currently show this token?) that no
# single node's read is trusted alone - now with a safe failure mode
# and a narrower, harder-to-spoof source.

from genlayer import *

import json
import datetime
import secrets


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------

MIN_CHECKIN_INTERVAL = 300          # 5 min floor, for testability
MIN_GRACE_PERIOD = 60
MIN_CONTEST_PERIOD = 60

MAX_BENEFICIARIES = 10
MAX_GUARDIANS = 10

BPS_TOTAL = 10000

# See changelog item 2 above: the only allowlisted attestation source.
# Narrower than "any URL" - an attacker needs to compromise a specific
# pre-named GitHub account, not just gain write access to wherever the
# owner happened to point.
ATTESTATION_URL_PREFIX = "https://raw.githubusercontent.com/"

HEX_DIGITS = set("0123456789abcdef")


@gl.evm.contract_interface
class _Payee:
    class View:
        pass

    class Write:
        pass


# ---------------------------------------------------------------------
# PURE HELPER for the nondet closure. No self, no storage - the
# sandbox that runs leader/validator closures cannot see either, and
# genvm-lint matches gl.nondet.* calls by literal scope, so the fetch
# itself still has to live inside the closures below, not in here.
# ---------------------------------------------------------------------


def _token_present(page_text: str, token: str) -> bool:
    return token in page_text


class LegacyVault(gl.Contract):
    counter: u256
    vaults: TreeMap[str, str]
    claims: TreeMap[str, str]      # "claim:<vault_id>:<addr>" -> "true"
    vault_ids: DynArray[str]

    def __init__(self):
        self.counter = u256(0)

    # =================================================================
    # HELPERS
    # =================================================================

    def _vault_key(self, vault_id: str) -> str:
        return "vault:" + str(vault_id)

    def _claim_key(self, vault_id: str, addr: str) -> str:
        return "claim:" + str(vault_id) + ":" + str(addr).strip().lower()

    def _now(self) -> int:
        # Confirmed deterministic against this repo's pinned runtime via
        # a standalone time-probe contract before shipping. If you're on
        # a different py-genlayer version, re-verify before trusting it.
        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    def _sender(self) -> str:
        return gl.message.sender_address.as_hex.lower()

    def _is_valid_address(self, addr: str) -> bool:
        if not addr.startswith("0x") or len(addr) != 42:
            return False

        return all(c in HEX_DIGITS for c in addr[2:])

    def _read_vault(self, vault_id: str):
        key = self._vault_key(vault_id)

        if key not in self.vaults:
            raise gl.vm.UserError("[EXPECTED] VAULT_NOT_FOUND")

        return json.loads(self.vaults[key])

    def _save_vault(self, vault) -> None:
        key = self._vault_key(vault["id"])
        self.vaults[key] = json.dumps(vault, sort_keys=True, separators=(",", ":"))

    def _require_owner(self, vault) -> None:
        if self._sender() != vault["owner"]:
            raise gl.vm.UserError("[EXPECTED] NOT_OWNER")

    def _pay(self, to_address: str, amount: int) -> None:
        if amount <= 0:
            return

        _Payee(Address(to_address)).emit_transfer(value=u256(amount))

    # =================================================================
    # CREATE / FUND
    # =================================================================

    @gl.public.write.payable
    def create_vault(
        self,
        beneficiaries_json: str,
        guardians_json: str,
        guardian_threshold: str,
        checkin_interval: str,
        grace_period: str,
        contest_period: str,
        attestation_url: str,
    ) -> str:
        owner = self._sender()

        try:
            beneficiaries = json.loads(beneficiaries_json)
        except Exception:
            raise gl.vm.UserError("[EXPECTED] BAD_BENEFICIARIES_JSON")

        if not isinstance(beneficiaries, list) or not (1 <= len(beneficiaries) <= MAX_BENEFICIARIES):
            raise gl.vm.UserError("[EXPECTED] BAD_BENEFICIARIES_COUNT")

        total_bps = 0
        seen_addresses = set()
        clean_beneficiaries = []

        for entry in beneficiaries:
            addr = str(entry.get("address", "")).strip().lower()

            if not self._is_valid_address(addr):
                raise gl.vm.UserError("[EXPECTED] BAD_BENEFICIARY_ADDRESS")

            if addr in seen_addresses:
                raise gl.vm.UserError("[EXPECTED] DUPLICATE_BENEFICIARY")

            seen_addresses.add(addr)

            try:
                bps = int(entry["bps"])
            except Exception:
                raise gl.vm.UserError("[EXPECTED] BAD_BPS")

            if bps <= 0:
                raise gl.vm.UserError("[EXPECTED] BAD_BPS")

            total_bps += bps
            clean_beneficiaries.append({"address": addr, "bps": bps})

        if total_bps != BPS_TOTAL:
            raise gl.vm.UserError("[EXPECTED] BPS_MUST_SUM_10000")

        try:
            guardians_raw = json.loads(guardians_json) if guardians_json.strip() else []
        except Exception:
            raise gl.vm.UserError("[EXPECTED] BAD_GUARDIANS_JSON")

        if not isinstance(guardians_raw, list) or len(guardians_raw) > MAX_GUARDIANS:
            raise gl.vm.UserError("[EXPECTED] BAD_GUARDIANS_COUNT")

        guardians = sorted({str(g).strip().lower() for g in guardians_raw})

        for g in guardians:
            if not self._is_valid_address(g):
                raise gl.vm.UserError("[EXPECTED] BAD_GUARDIAN_ADDRESS")

        try:
            threshold = int(guardian_threshold)
        except Exception:
            raise gl.vm.UserError("[EXPECTED] BAD_THRESHOLD")

        if guardians and not (1 <= threshold <= len(guardians)):
            raise gl.vm.UserError("[EXPECTED] BAD_THRESHOLD")

        try:
            interval = int(checkin_interval)
            grace = int(grace_period)
            contest = int(contest_period)
        except Exception:
            raise gl.vm.UserError("[EXPECTED] BAD_TIMING")

        if interval < MIN_CHECKIN_INTERVAL:
            raise gl.vm.UserError("[EXPECTED] INTERVAL_TOO_SHORT")

        if grace < MIN_GRACE_PERIOD:
            raise gl.vm.UserError("[EXPECTED] GRACE_TOO_SHORT")

        if contest < MIN_CONTEST_PERIOD:
            raise gl.vm.UserError("[EXPECTED] CONTEST_TOO_SHORT")

        url = str(attestation_url).strip()

        if url != "" and not url.startswith(ATTESTATION_URL_PREFIX):
            # See changelog item 2: only a pre-named, identity-bound
            # source is accepted - not an arbitrary page.
            raise gl.vm.UserError("[EXPECTED] ATTESTATION_SOURCE_NOT_ALLOWED")

        # A random token the owner is instructed to publish somewhere
        # they control, as long as they're alive and in charge. Its
        # presence is what validators check for later - never reveal
        # it in a way that lets someone else plant it for them.
        token = "legacyvault-" + secrets.token_hex(12)

        now = self._now()
        self.counter = u256(int(self.counter) + 1)
        vault_id = str(int(self.counter))

        vault = {
            "id": vault_id,
            "owner": owner,
            "beneficiaries": clean_beneficiaries,
            "guardians": guardians,
            "guardian_threshold": str(threshold),
            "guardian_votes": {},
            "checkin_interval": str(interval),
            "grace_period": str(grace),
            "contest_period": str(contest),
            "attestation_url": url,
            "attestation_token": token,
            "balance_wei": str(int(gl.message.value)),
            "distributed_wei": "0",
            "status": "active",
            "last_checkin": str(now),
            "pending_since": "",
            "pending_reason": "",
            "created_at": str(now),
        }

        self._save_vault(vault)
        self.vault_ids.append(vault_id)

        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    @gl.public.write.payable
    def deposit(self, vault_id: str) -> str:
        vault = self._read_vault(vault_id)
        amount = int(gl.message.value)

        if amount <= 0:
            raise gl.vm.UserError("[EXPECTED] ZERO_VALUE")

        if vault["status"] not in ("active", "pending_release"):
            raise gl.vm.UserError("[EXPECTED] VAULT_CLOSED")

        vault["balance_wei"] = str(int(vault["balance_wei"]) + amount)
        self._save_vault(vault)

        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    # =================================================================
    # CHECK-IN / CANCEL
    # =================================================================

    @gl.public.write
    def check_in(self, vault_id: str) -> str:
        vault = self._read_vault(vault_id)
        self._require_owner(vault)

        if vault["status"] not in ("active", "pending_release"):
            raise gl.vm.UserError("[EXPECTED] VAULT_CLOSED")

        # Checking in is the unconditional proof of life: it both
        # resets the clock and cancels any release in progress,
        # regardless of how that release was triggered.
        vault["status"] = "active"
        vault["last_checkin"] = str(self._now())
        vault["pending_since"] = ""
        vault["pending_reason"] = ""
        vault["guardian_votes"] = {}

        self._save_vault(vault)
        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    @gl.public.write
    def guardian_vote_emergency(self, vault_id: str) -> str:
        vault = self._read_vault(vault_id)
        voter = self._sender()

        if voter not in vault["guardians"]:
            raise gl.vm.UserError("[EXPECTED] NOT_GUARDIAN")

        if vault["status"] != "active":
            raise gl.vm.UserError("[EXPECTED] VAULT_NOT_ACTIVE")

        vault["guardian_votes"][voter] = str(self._now())

        if len(vault["guardian_votes"]) >= int(vault["guardian_threshold"]):
            vault["status"] = "pending_release"
            vault["pending_since"] = str(self._now())
            vault["pending_reason"] = "guardian_vote"

        self._save_vault(vault)
        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    # =================================================================
    # RELEASE  (the nondet consensus step - now fail-closed)
    # =================================================================

    @gl.public.write
    def request_release(self, vault_id: str) -> str:
        vault = self._read_vault(vault_id)

        if vault["status"] != "active":
            raise gl.vm.UserError("[EXPECTED] VAULT_NOT_ACTIVE")

        deadline = (
            int(vault["last_checkin"])
            + int(vault["checkin_interval"])
            + int(vault["grace_period"])
        )

        if self._now() < deadline:
            raise gl.vm.UserError("[EXPECTED] BEFORE_DEADLINE")

        url = str(vault["attestation_url"])

        if url == "":
            # No external attestation configured - the check-in timer
            # alone is the liveness signal.
            vault["status"] = "pending_release"
            vault["pending_since"] = str(self._now())
            vault["pending_reason"] = "timeout"
            self._save_vault(vault)
            return json.dumps(vault, sort_keys=True, separators=(",", ":"))

        token = str(vault["attestation_token"])

        # Copied into locals before the closures are defined - self and
        # storage handles must not cross into the non-deterministic
        # sandbox. gl.nondet.web is spelled out literally inside each
        # closure so genvm-lint's scope match holds.
        #
        # FAIL-CLOSED: neither closure below swallows a fetch/render
        # exception into a False/"not found" result anymore. A leader
        # whose fetch fails simply raises - the whole non-deterministic
        # round produces no result, nothing gets saved, and the call
        # can be retried once the source is reachable again. A
        # validator whose own fetch fails returns False - disagreement,
        # never silent agreement - so an outage can never itself
        # supply the "consensus" that would let a release through.
        def leader_fn():
            text = gl.nondet.web.render(url, mode="text")
            found = _token_present(text, token)

            return {"found": found}

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False

            leader_data = leader_result.calldata

            if not isinstance(leader_data, dict):
                return False

            try:
                text = gl.nondet.web.render(url, mode="text")
            except Exception:
                # Cannot confirm anything myself right now - treat
                # that as disagreement with whatever the leader
                # claimed, never as confirmation of "not found".
                return False

            found = _token_present(text, token)

            return leader_data.get("found") == found

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        if not isinstance(result, dict) or "found" not in result:
            raise gl.vm.UserError("[EXTERNAL] BAD_CONSENSUS_SHAPE")

        if bool(result["found"]):
            # Owner's token is still live on their attested page: they
            # still control it, so refuse release and leave the timer
            # exactly as it was. They still need to call check_in to
            # actually reset the clock.
            raise gl.vm.UserError("[EXPECTED] ATTESTATION_STILL_LIVE")

        vault["status"] = "pending_release"
        vault["pending_since"] = str(self._now())
        vault["pending_reason"] = "attestation_absent"

        self._save_vault(vault)
        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    @gl.public.write
    def cancel_release(self, vault_id: str) -> str:
        vault = self._read_vault(vault_id)
        self._require_owner(vault)

        if vault["status"] != "pending_release":
            raise gl.vm.UserError("[EXPECTED] NOT_PENDING")

        vault["status"] = "active"
        vault["last_checkin"] = str(self._now())
        vault["pending_since"] = ""
        vault["pending_reason"] = ""
        vault["guardian_votes"] = {}

        self._save_vault(vault)
        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    @gl.public.write
    def finalize_release(self, vault_id: str) -> str:
        vault = self._read_vault(vault_id)

        if vault["status"] != "pending_release":
            raise gl.vm.UserError("[EXPECTED] NOT_PENDING")

        deadline = int(vault["pending_since"]) + int(vault["contest_period"])

        if self._now() < deadline:
            raise gl.vm.UserError("[EXPECTED] CONTEST_PERIOD_NOT_OVER")

        vault["status"] = "released"
        vault["distributed_wei"] = vault["balance_wei"]

        self._save_vault(vault)
        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    # =================================================================
    # CLAIM
    # =================================================================

    @gl.public.write
    def claim(self, vault_id: str) -> str:
        vault = self._read_vault(vault_id)

        if vault["status"] != "released":
            raise gl.vm.UserError("[EXPECTED] NOT_RELEASED")

        user = self._sender()
        claim_key = self._claim_key(vault_id, user)

        if claim_key in self.claims:
            raise gl.vm.UserError("[EXPECTED] ALREADY_CLAIMED")

        share_bps = 0
        for b in vault["beneficiaries"]:
            if b["address"] == user:
                share_bps = int(b["bps"])
                break

        if share_bps <= 0:
            raise gl.vm.UserError("[EXPECTED] NOT_A_BENEFICIARY")

        total = int(vault["distributed_wei"])
        payout = total * share_bps // BPS_TOTAL

        # Effects before interaction.
        self.claims[claim_key] = "true"
        self._pay(user, payout)

        return json.dumps(
            {"vault_id": vault_id, "beneficiary": user, "payout": str(payout)},
            sort_keys=True,
            separators=(",", ":"),
        )

    @gl.public.write
    def owner_withdraw_unlocked(self, vault_id: str, amount_wei: str) -> str:
        # Owner can pull funds back out while the vault is still active
        # and nothing is pending - it's their money until it isn't.
        vault = self._read_vault(vault_id)
        self._require_owner(vault)

        if vault["status"] != "active":
            raise gl.vm.UserError("[EXPECTED] VAULT_NOT_ACTIVE")

        amount = int(amount_wei)
        balance = int(vault["balance_wei"])

        if amount <= 0 or amount > balance:
            raise gl.vm.UserError("[EXPECTED] BAD_AMOUNT")

        vault["balance_wei"] = str(balance - amount)
        self._save_vault(vault)
        self._pay(vault["owner"], amount)

        return json.dumps(vault, sort_keys=True, separators=(",", ":"))

    # =================================================================
    # VIEWS
    # =================================================================

    @gl.public.view
    def get_vault(self, vault_id: str) -> str:
        return json.dumps(
            self._read_vault(vault_id), sort_keys=True, separators=(",", ":")
        )

    @gl.public.view
    def list_vaults(self) -> str:
        ids = [str(x) for x in self.vault_ids]
        return json.dumps(ids, separators=(",", ":"))

    @gl.public.view
    def has_claimed(self, vault_id: str, addr: str) -> str:
        key = self._claim_key(vault_id, addr)
        return "true" if key in self.claims else "false"

    @gl.public.view
    def get_counter(self) -> str:
        return str(int(self.counter))

