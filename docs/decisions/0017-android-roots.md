# Web ADR 0017 — Android's frozen roots: Mozilla's root list with the system's, below Android 14

**Status:** accepted 2026-11-20. The owner: "Yes, I accept both 1 and 2" (§6, both as recommended). Implemented (§8).
Asked for by the owner: IPos on a Sunmi V2 (Android 7.1.1) cannot sign in to its
production server, and of the fixes offered (an RSA certificate on the server, a root in IPos, a root list in
`web_tls`) the owner chose "Root bundle in web_tls".
**Scope:** `web_tls` on Android only: `trustSystem` (`wt_cfg_trust_default`'s Android branch), an embedded root
list and the tool that refreshes it, a licence notice. No API change; no change on other systems.

## 1. Context

- **What fails.** `https://api.rawabialqimma.com` has a Let's Encrypt ECDSA certificate whose chain is
  `YE2 → Root YE → ISRG Root X2` (checked with `openssl s_client -showcerts`, 2026-11-20). Verified against the Sunmi
  V2's own roots (`/system/etc/security/cacerts`, 156 files pulled with adb) it fails: `error 20 at 2 depth lookup:
  unable to get local issuer certificate`. The device has ISRG Root X1 (`6187b673.0`) and not X2. Verified against
  Mozilla's current list (curl's extract of 2026-11-05) the same chain is `OK`.
- **Why.** Below Android 14 the system's roots are part of the system image: they are the roots of the day the
  device's release was built, and a device that gets no system updates (most tills, handhelds and old phones) never
  gains a new one. From Android 14 they are updatable (`/apex/com.android.conscrypt/cacerts`), which `web_tls`
  already reads first (PR #45, `tls_shim.c` `android_roots`). Every Let's Encrypt ECDSA certificate issued from its
  2025 hierarchy ends at X2, so every such server fails on a device whose frozen roots predate X2.
  Chrome on the device fails the same way; the app's TLS code reads the system's roots correctly.
- **What `web_tls` trusts now.** `TlsConfig.trustSystem` → `wt_cfg_trust_default`: OpenSSL's default locations, and on
  Android the updatable directory, else the system image's. `trustPem` adds roots a program gives.

## 2. Options

1. **Mozilla's root list in `web_tls`, added to the system's below Android 14 (chosen).** Every CeeSafe program on an
   old Android trusts what current browsers trust, with no change to it or to its server.
2. **The server's certificate with an RSA key** (`YR → Root YR → ISRG Root X1`, which Android 7.1.1 has). Fixes this
   server for every client, but each server must know to do it, and it is not ours to change.
3. **The root in IPos** (`trustPem` with X2). Works today, but every app must find and carry its own roots.
4. **Mozilla's list on every Android.** Android 14 and later keep their roots current; adding a second list there only
   widens trust past what the system decided (a root the system removed would still be trusted).
5. **Android's own `X509TrustManager` through JNI.** Judges against the same frozen roots: it fixes nothing.

## 3. Decision

1. **The list.** `packages/web_tls/c/roots_mozilla.h`: the certificates of curl's extract of Mozilla's root store
   (`https://curl.se/ca/cacert.pem`, which states the `certdata.txt` revision and date it came from), as one C string
   of PEM, with the extract's date and SHA-256 in a comment and a `WT_MOZILLA_ROOTS_DATE` macro. It is compiled only
   on Android (`#if defined(__ANDROID__)`): about 200 KB in an Android program that links `web_tls`, nothing elsewhere.
2. **When it is used.** On Android, `wt_cfg_trust_default` loads the system's roots as now; when it took them from the
   system image (`/apex/com.android.conscrypt/cacerts` absent: Android 13 or older), it also adds every certificate
   of the list to the store. The result is the union: a root either has is trusted. The verification is otherwise the
   same (OpenSSL's chain building, the host name check of ADR 0002 §3).
3. **Refreshing it.** `tools/update_roots.py` downloads `cacert.pem` and its `cacert.pem.sha256` from curl.se, checks
   one against the other, and rewrites `roots_mozilla.h`. It is run before each CeeSafe.Web release (ADR 0005's
   release checklist gains the line) and whenever a root changes that matters; the commit says the extract's date.
4. **Licence.** Mozilla's `certdata.txt` is MPL 2.0; curl publishes the extract under it. `COPYING.MPL` (the licence
   text) is added beside `COPYING.OPENSSL`, and the README's licence section names the list.

## 4. Consequences

- A CeeSafe program on Android 7 to 13 reaches servers whose roots are newer than the device: IPos signs in to its
  production server on the Sunmi V2.
- What such a program trusts is the device's roots plus Mozilla's of the extract's date. A root Mozilla has since
  removed stays trusted if the device has it (as today); one Mozilla removes after the extract stays trusted by that
  build until the list is refreshed and the program rebuilt.
- Android programs are about 200 KB larger.
- No change on Android 14 and later, iOS, macOS, Windows or Linux.

## 5. Tests

- `packages/web_tls/tests`: a configuration's `trustSystem` on a build that includes the list (a test-only switch
  compiles it on the host) loads at least 100 roots; a fixed chain to ISRG Root X2 (YE2's test fixture, the
  certificates committed) verifies with the list and not without it, at a fixed time.
- `tools/update_roots.py --check`: the committed header is the extract it names (SHA-256), run in CI.
- On the device, by the owner: IPos on the Sunmi V2 signs in to `https://api.rawabialqimma.com`.

## 6. Questions for the owner

1. Accept Mozilla's list (through curl's extract) as the source, added to the system's below Android 14 only? *(Recommended.)*
2. Accept refreshing it at each release (§3.3), rather than on a timetable? *(Recommended.)*

## 7. Order of work

1. `update_roots.py`, `roots_mozilla.h`, `COPYING.MPL`.
2. The Android branch of `wt_cfg_trust_default`; the tests.
3. A release of CeeSafe.Web; IPos's pin moved to it; the check on the Sunmi V2.

## 8. As built (2026-11-20)

- `tools/update_roots.py` writes `packages/web_tls/c/roots_mozilla.pem` (curl's extract byte for byte, its SHA-256
  checked against curl.se's `cacert.pem.sha256`) and `roots_mozilla.h` from it; `--check` (no network) runs in CI.
  The first extract: Mozilla's roots of 2026-11-05, 118 certificates, SHA-256 `427c0642…8497285`.
- `tls_shim.c` `android_roots`: Mozilla's list is added when the roots came from the system image, and also when no
  system directory could be read (a store of none would trust nothing).
- **Tests, changed from §5.** `web_tls` has no switch to compile the Android branch on the host, so the host tests
  read the same list from `roots_mozilla.pem` (which `--check` ties to the header): `mozilla_roots_load_as_trust`
  (at least 100 certificates; `trustPem` takes them) and `mozilla_roots_hold_isrg_root_x2` (the list holds ISRG Root
  X2, compared with Let's Encrypt's own copy, `tests/certs/isrg-root-x2.pem`). A chain to X2 cannot be checked on the
  host through the API (it has no verify call without a handshake, and no server with Let's Encrypt's key). Run
  2026-11-20 on macOS: `ceesafec test` in `packages/web_tls`, 20 passed, 0 failed. By hand: `openssl verify` of
  `api.rawabialqimma.com`'s chain fails with the Sunmi V2's 156 roots and passes with the list. The Android branch
  is checked on the device (§5's last item): 2026-11-20, IPos built for armeabi-v7a with `web_tls` at this branch, on
  the Sunmi V2 (Android 7.1.1), signing in to `https://api.rawabialqimma.com` with a user name that does not exist:
  the server's answer, "Invalid User name or password", where the same build on the old `web_tls` said "TLS
  certificate rejected".
- Web ADR 0005 gains the release step as an amendment.
