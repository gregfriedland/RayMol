---
name: cut-ios-release
description: Use when submitting, pushing, or publishing a RayMol build to the **iOS / iPadOS App Store** — "ship it on iPhone", "iOS release", "submit the iPad app", "new iOS App Store version", "send the iOS build to App Review", or getting a version onto the iOS store — even if no version or steps are named. Also use when an iOS submission was rejected (ITMS-91061, ITMS-90208, INVALID_BINARY) and needs recovering. NOT for TestFlight betas (Xcode Cloud ships those on its own), the Mac App Store (`cut-mas-release`), or the Sparkle/DMG release (`cut-macos-release`).
---

# Cut an iOS App Store RayMol release

Ships RayMol to the iOS/iPadOS App Store: a **fresh, hand-made archive** of the release tag, signed with the App Store Connect API key, uploaded, verified, then submitted by the human. Same App Store Connect record as the Mac app (App ID `6781513038`, team `VT99UQUQ89`, bundle `io.raymol.RayMol`), but a separate platform version with its own build train and metadata.

Rhythm: **build → verify → upload → TestFlight smoke test → prepare version → human submits.** Submit for Review is outward-facing and irreversible, and so is attaching a build to a version (see non-negotiable 7), so both sit at the end.

## Non-negotiables

1. **No TestFlight build can be submitted — always archive fresh.** Xcode Cloud archives every beta with `buildDistributionAudience: INTERNAL_ONLY`, which is permanent per build: those builds can never go to App Review or external testing (`docs/ios-beta-pipeline.md`). They also carry the wrong version (`scripts/nightly_version.sh` stamps one patch above `project.yml`) and a `RayMolBetaLabel`. Don't look for a beta to promote.
2. **Build from the release tag, with a clean device core and the fingerprinted deps.** `archive_appstore.sh iOS` does **not** build the C++ core (its macOS branch does, the iOS branch doesn't) — it links whatever `build_ios_device/libpymol_core.a` is lying around. Delete and rebuild it from the tag. Stage `deps_ios` from the published `ios-deps-<fingerprint>` artifact exactly as Xcode Cloud does; never reuse a hand-built `deps_ios` from the main clone, which can silently predate the fingerprint.
3. **Use the tag's build number; don't chase the CI counter.** An iOS build number only has to be unique (and higher) **within its own version's train**. Xcode Cloud's 100+ numbers live on the next-patch beta train and never collide. Precedent: iOS 1.10.0 shipped as build 27 while betas were in the 60s. So a `1.12.0` release uses `CURRENT_PROJECT_VERSION` from the tag (e.g. 32). A rejected or failed upload **consumes** its number — re-archive with `CURRENT_PROJECT_VERSION=<N+1>` on the xcodebuild line.
4. **Sign with the Admin-role API key, not an Xcode account.** The Mac may have no Apple ID in Xcode. The script's archive step works; its `-exportArchive` uses account signing and fails ("No Accounts" / no iOS Distribution cert). Re-export by hand with `-authenticationKey*` flags (Step 3), which mints the Distribution cert + profile through cloud signing. An App-Manager-role key fails with "Cloud signing permission error" — it must be Admin.
5. **`VALID` is not safe. Inspect the IPA itself.** ITMS-91061 (missing OpenSSL privacy manifest) fires at **Submit for Review**, not at upload: `altool --validate-app` says VERIFY SUCCEEDED, the build processes to `VALID`, and App Review then drops the version into `INVALID_BINARY`. The only pre-submit proof is the manifest inside the `_ssl` and `_hashlib` frameworks (Step 4). Separately, ITMS-90208 arrives by **email** minutes after "UPLOAD SUCCEEDED" — poll until `VALID` before trusting an upload.
6. **iOS "What's New" is its own copy.** Start from `docs/release-notes/vX.Y.Z.md`, then remove: the Sparkle "Check for Updates / in-app updater" line, Homebrew, MCP / AI-copilot and anything else compiled out of iOS (`RayMolBuild.mcpEnabled` is macOS-only), Mac-only UI, and experimental features behind a flag (Binder Design). Check each remaining feature on an iPad before claiming it.
7. **Attaching a build can submit it.** If the platform has a leftover `reviewSubmission` in `UNRESOLVED_ISSUES` (e.g. from an earlier `INVALID_BINARY`), attaching a VALID build resolves it and App Store Connect moves the version **into review on its own**, with no submit call. So: check submissions first (cancel a stale one), set `releaseType: MANUAL`, fill in **all** metadata, and attach the build **last**. Metadata locks the moment review starts.
8. **The human signs in and clicks Submit for Review.** Never enter Apple ID credentials. Prepare everything; stop at the button.

## Where things live

- **Version:** `swiftui/project.yml` — `MARKETING_VERSION`, `CURRENT_PROJECT_VERSION`. Committed `RAYMOL_BETA_LABEL` must stay `""` (a value marks the store build a beta).
- **Archive/export:** `swiftui/archive_appstore.sh iOS` → `swiftui/build_archive/RayMol-iOS.xcarchive`, export options at `/tmp/raymol-export-iOS.plist`, IPA in `swiftui/build_export/iOS/`. It runs `xcodegen generate`; **never commit the regenerated `project.pbxproj`**.
- **Device core:** `bash swiftui/build_ios.sh device` → `build_ios_device/libpymol_core.a`. Input check: `bash scripts/assert_ios_build_inputs.sh`.
- **Deps artifact:** `bash scripts/ios_deps_fingerprint.sh` → `https://github.com/javierbq/RayMol/releases/download/ios-deps-<fp>/deps_ios-<fp>.tar.gz` (+ `.sha256`). Step 3/7 of `swiftui/ci_scripts/ci_post_clone.sh` is the reference.
- **Store copy:** `docs/appstore/listing.md` (description, keywords), `docs/appstore/review-notes.md` (reviewer notes; any demo key inserted there is never committed), `docs/appstore/screenshots/`.
- **API key:** `~/.appstoreconnect/private_keys/AuthKey_<id>.p8`; export `ASC_KEY_ID` + `ASC_ISSUER` (ASC ▸ Users and Access ▸ Integrations). The IDs are account identifiers kept out of this public repo; never write them into it.
- **Status:** `.claude/skills/cut-mas-release/scripts/asc_status.py` lists recent builds for both platforms, but only the **8 newest App Store versions across both** — macOS releases often push every iOS version off the list. Query iOS versions directly (Step 1).

See `references/gotchas.md` for the packaging blockers this build already fixes, rejection-mail recovery, and the metadata/screenshot API.

## Step 0 — Before the tag (during the macOS release candidate)

No CI compiles iOS, and shared SwiftUI code has broken the iOS build before. While `cut-macos-release` Step 2 is building its RC, compile the iOS target too (simulator is enough), and look at any new UI on an iPad simulator. Fix breakage in the release PR, before the tag exists.

## Step 1 — Check App Store Connect state

```bash
python3 .claude/skills/cut-mas-release/scripts/asc_status.py      # builds (both platforms)
```
Then query iOS directly with the same JWT (see the script's `_token`/`_get`):
- `GET /v1/apps/6781513038/appStoreVersions?filter[platform]=IOS` — newest `READY_FOR_SALE` is the live version; nothing may be `WAITING_FOR_REVIEW` / `IN_REVIEW` / `PENDING_DEVELOPER_RELEASE`.
- `GET /v1/reviewSubmissions?filter[app]=6781513038` — any `IOS` submission in `UNRESOLVED_ISSUES` is **live** (non-negotiable 7); cancel it with `PATCH /v1/reviewSubmissions/{id}` `{"data":{"type":"reviewSubmissions","id":"<id>","attributes":{"canceled":true}}}` before going further.

- `GET /v1/builds?filter[app]=6781513038&filter[preReleaseVersion.version]=X.Y.Z&filter[preReleaseVersion.platform]=IOS` — builds already on this version's train. If an earlier attempt used the tag's number, it is consumed: take the next free one.

Confirm with the user: version (normally the same tag as the other channels), build = the tag's `CURRENT_PROJECT_VERSION` unless that check says otherwise.

## Step 2 — Worktree at the tag, deps, clean core

```bash
git worktree add -b ios/vX.Y.Z ../raymol-ios-X.Y.Z vX.Y.Z && cd ../raymol-ios-X.Y.Z
FP=$(bash scripts/ios_deps_fingerprint.sh); T=deps_ios-$FP.tar.gz
B=https://github.com/javierbq/RayMol/releases/download/ios-deps-$FP
curl -fL -o $T $B/$T && curl -fL -o $T.sha256 $B/$T.sha256 && shasum -a 256 -c $T.sha256 && tar -xzf $T && rm -f $T $T.sha256
# no artifact for this fingerprint → run the "iOS deps artifact" GitHub workflow; never build deps inline
rm -rf build_ios_device && bash swiftui/build_ios.sh device
bash scripts/assert_ios_build_inputs.sh                          # arm64 core, Python.framework, numpy, Bio present
```
Run outside any command sandbox (network, `sysctl`).

## Step 3 — Archive, then export with the API key

```bash
cd swiftui && ./archive_appstore.sh iOS    # archive succeeds; export fails without an Xcode account — expected
xcodebuild -exportArchive -archivePath build_archive/RayMol-iOS.xcarchive \
  -exportOptionsPlist /tmp/raymol-export-iOS.plist -exportPath build_export/iOS \
  -allowProvisioningUpdates \
  -authenticationKeyPath ~/.appstoreconnect/private_keys/AuthKey_$ASC_KEY_ID.p8 \
  -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER"
git checkout -- PyMOLViewer.xcodeproj/project.pbxproj   # drop xcodegen's regeneration
```
If the script's export already succeeded (Xcode signed in), skip the manual export.

## Step 4 — Verify the IPA before uploading

```bash
IPA=build_export/iOS/RayMol.ipa; T=$(mktemp -d); unzip -q "$IPA" -d "$T"; APP=$(echo "$T"/Payload/*.app)
plutil -extract CFBundleShortVersionString raw "$APP/Info.plist"   # X.Y.Z
plutil -extract CFBundleVersion raw "$APP/Info.plist"              # the build number
plutil -extract RayMolBetaLabel raw "$APP/Info.plist" 2>&1 | grep -q 'No value' && echo "not a beta OK"
codesign -dvv "$APP" 2>&1 | grep Authority                          # Apple Distribution: … (VT99UQUQ89)
for f in _ssl _hashlib; do ls "$APP"/Frameworks/*$f*.framework/PrivacyInfo.xcprivacy >/dev/null 2>&1 \
  && echo "$f manifest OK" || echo "!! $f MISSING PrivacyInfo — STOP (ITMS-91061)"; done
rm -rf "$T"
```
Require every line. A wrong version means the wrong checkout — STOP.

## Step 5 — Validate, upload, wait for VALID

```bash
xcrun altool --validate-app -f "$IPA" -t ios --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER"
xcrun altool --upload-app   -f "$IPA" -t ios --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER"
```
Poll `asc_status.py` until the `IOS` build shows `VALID` (minutes). Watch the review-contact mailbox (gotchas) for ITMS-90208 in the meantime.

## Step 6 — Smoke-test the exact binary through TestFlight

This manual upload is **not** internal-only, so it can go to the internal `Beta` group. Install it from TestFlight on an iPhone and an iPad and check: launch with a restored session (no watchdog hang), the release's headline features, and the What's New carousel for this version. This is the only way to run the store-signed build on hardware (Debug builds can't launch on a device).

## Step 7 — Prepare the version (metadata first, build last)

Web UI (human signed in) or API:
1. Create the **iOS** version `X.Y.Z` (`POST /v1/appStoreVersions`, `platform: IOS`, `releaseType: MANUAL`). In the web UI the iOS `+` is labelled `Add iOS App`; confirm the platform via the API afterwards.
2. Set **What's New** (non-negotiable 6) on the version localization. Description, keywords and screenshots carry over; refresh screenshots only if the headline feature needs showing.
3. Confirm `releaseType` is `MANUAL`, and Step 1's submission check still holds.
4. **Last:** attach the build (`PATCH /v1/appStoreVersions/{id}/relationships/build`). Then **Add for Review** → the human clicks **Submit for Review**.

## Step 8 — After submit

- State goes to `WAITING_FOR_REVIEW`. If it drops to `INVALID_BINARY` within minutes, it's a binary check (ITMS-91061 is the known one) — read the mail (gotchas), fix, re-archive with a **new build number**, and before re-attaching, cancel the now-`UNRESOLVED_ISSUES` submission (non-negotiable 7).
- The rejection reason is **not** in the ASC API. It is emailed to the App Review contact (see gotchas for the Apple Mail grep).
- Approved → `PENDING_DEVELOPER_RELEASE`. Remind the user to click **Release This Version**.

## Done

Report: version/build submitted, that the IPA was Apple-Distribution-signed with both OpenSSL manifests and no beta label, the TestFlight smoke-test result, the What's New text used, and the current state (`WAITING_FOR_REVIEW`, manual release pending approval).
