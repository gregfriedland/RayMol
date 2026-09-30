# iOS App Store release — failure modes & recovery

Specifics from RayMol's iOS submissions (first: 1.8.0 build 22, 2026-07-17). Read when a step misbehaves.

## The packaging blockers this build already fixes — don't regress them

iOS embeds every Python extension `.so` as a framework; the Mac app ships them directly and hit none of these. Each surfaced one at a time from Apple validation. The fixes live in `swiftui/project.yml` ("iOS: Prepare Python binary modules" phase), `swiftui/PyMOLViewer/Resources/dylib-Info-template.plist` and `scripts/build_numpy_ios.sh`. If a new rejection cites one, the mechanism below broke:

| Symptom | Cause | Fix in place |
|---|---|---|
| Invalid framework bundle ID (`.-pcg64`) | `tr '_' '-'` made leading-dash segments | `sed -E 's/\.-+/./g'` in the prepare phase |
| altool: "Cannot determine the Apple ID from Bundle ID …numpy…" | framework template said `CFBundlePackageType APPL` | template says `FMWK` |
| 90124 / 90171 on numpy | meson links extensions `MH_BUNDLE`; frameworks must be `MH_DYLIB` | `build_numpy_ios.sh` rewrites each `.so` with lief (file type DYLIB + `LC_ID_DYLIB`) |
| 90171 "standalone library" | `libnpymath.a` / `libnpyrandom.a` bundled | `build_numpy_ios.sh` deletes `*.a` |
| 90474 iPad orientations | iPad must declare all four | `INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad` lists all four |
| ITMS-90208 (email after upload) | framework `MinimumOSVersion` below the binaries' minos | prepare phase pins it to `$IPHONEOS_DEPLOYMENT_TARGET` |
| ITMS-91061 (at **submit**) | `_ssl` / `_hashlib` statically link OpenSSL, a listed SDK needing a privacy manifest | prepare phase copies `Resources/openssl-PrivacyInfo.xcprivacy` into both; `_ssl` + `_hashlib` are the only OpenSSL carriers (verified by `strings` over all embedded frameworks) |

## Reading a rejection

The ASC API only says `reviewSubmissions … state=UNRESOLVED_ISSUES` with the item `REJECTED`; `/v1/resolutionCenterThreads` 404s. Apple mails the reason to the **App Review contact** (`GET /v1/appStoreVersions/{id}/appStoreReviewDetail` → `contactEmail`), which is not the work Google account — the Gmail tools find nothing. That mailbox syncs into local Apple Mail:

```bash
cd ~/Library/Mail/V10 && find . -name '*.emlx' -newermt 2026-01-01 -print0 | xargs -0 grep -l -i 'itms-'
# decode quoted-printable:
perl -0777 -pe 's/=\r?\n//g; s/=([0-9A-F]{2})/chr(hex($1))/ge' <file>
```
(Replace the date with the submission day.)

## Recovering from INVALID_BINARY

1. Fix the cause, re-archive with a **new** build number (the rejected one is consumed).
2. **Before attaching the new build**, cancel the version's `UNRESOLVED_ISSUES` submission — attaching a VALID build to it re-enters review automatically (the tells: state flips to `IN_REVIEW`, and metadata PATCHes start returning 409 `STATE_ERROR` "cannot be edited at this time").
3. Re-set What's New while the version is still editable, then attach, then let the human submit.

## Metadata & screenshots via the API

- Create a version: `POST /v1/appStoreVersions` with `platform: IOS`, `versionString`, `releaseType: MANUAL`.
- Attach a build: `PATCH /v1/appStoreVersions/{id}/relationships/build` (204). Do it last.
- Text lives on `appStoreVersionLocalizations`. Updates use `whatsNew`; a platform's **first-ever** version uses `description` instead (`whatsNew` returns 409 there).
- Screenshots: create an `appScreenshotSet` per display type → `POST /v1/appScreenshots {fileName, fileSize}` → PUT the bytes per `uploadOperations` → `PATCH {uploaded: true, sourceFileChecksum: <md5 hex>}` → poll `assetDeliveryState` until `COMPLETE`. Newest sizes are accepted: iPhone 6.9" (1320×2868) → `APP_IPHONE_67`; iPad 13" (2752×2064) → `APP_IPAD_PRO_3GEN_129`. They carry over between versions once uploaded.
- The API's version→build relationship can read empty right after attaching; trust the web UI for attachment and the API for state.

## Signing

- `IDEProvisioningTeams` empty / "No Accounts" at export = no Apple ID in Xcode. Not a blocker: export with the `-authenticationKey*` flags (SKILL Step 3).
- "Cloud signing permission error" = the key isn't Admin role.
- `security find-identity` showing no Apple Distribution identity is normal (cloud-managed signing); don't chase a missing certificate.

## Build numbers

- Unique and increasing per **platform + version train**. The beta train (`X.Y.Z+1`, Xcode Cloud numbers) never collides with the release train.
- A train closes permanently once its version ships: uploading to a shipped version fails with ITMS-90186 "train version is closed" / ITMS-90062. That's why betas ride the next patch.
- A stranded prerelease (iOS `1.10.0` build 21 from the retired next-minor scheme) exists and is harmless; don't try to reuse it.
