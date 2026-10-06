# Install ShapeMeasure through TestFlight

This package contains source, an Xcode project, and an optional archive helper. It does not contain a signed app or an uploaded TestFlight build. The iOS interface still needs its first Xcode compile and physical-device check. Your Apple Developer Program membership is already in place.

To **build in the cloud with Codemagic**, follow [CODEMAGIC.md](CODEMAGIC.md); local Xcode installation is optional. The steps below describe the local Mac route.

**1. Install a compatible Xcode on your Mac.**

Open Apple menu → About This Mac and check the macOS version and chip or processor. Apple currently requires iPhone uploads to use **Xcode 26 or later with the iOS 26 SDK or later**. Building with that SDK does not raise this app's iOS 17 minimum deployment target.

Use a compatible, non-beta Xcode release. Apple's current compatibility table lists Xcode 26.2 and 26.3 for macOS Sequoia 15.6 through Tahoe 26.x. Newer releases have higher Mac requirements. The current Mac App Store listing for Xcode requires macOS 26.6 or later and an Apple M1 or later chip, so check compatibility before installing. If needed, get an older compatible Xcode 26 release from [Apple Developer Downloads](https://developer.apple.com/download/all/?q=Xcode). A Mac that cannot run a supported macOS/Xcode combination needs another compatible Mac for the build.

Open Xcode once, complete its setup, and install iOS platform support when prompted. In Xcode Settings → Apple Accounts (called Accounts in some releases), sign in with the Apple Account enrolled in your paid developer team. If using the optional script, select this Xcode under Settings → Locations → Command Line Tools.

**2. Open and sign the project.**

Unzip the download and open `ShapeMeasure.xcodeproj`. Select the blue project icon, then the **ShapeMeasure target**.

- In Signing & Capabilities, keep **Automatically manage signing** enabled and select your paid developer team.
- Replace `com.example.ShapeMeasure` with your own unique, explicit bundle identifier. For example, replace the company part of `com.yourcompany.shapemeasure` with your own name or domain. Use the same identifier in App Store Connect.
- In General, keep **Version 0.1.0** and **Build 1** for the first upload. Use Build 2, 3, and so on for later uploads. Each uploaded build needs a new build number.
- Keep the minimum deployment target at **iOS 17.0**.

Xcode normally registers the identifier while managing signing. If it is absent from App Store Connect's Bundle ID menu, register an explicit App ID with that identifier under [Apple Developer → Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/identifiers/list). No push, iCloud, or other optional capabilities are needed by this version.

**3. Compile and check the app before uploading.**

Connect and unlock your Pro iPhone, trust the Mac if prompted, select the phone as Xcode's run destination, then press Run. Xcode may ask you to enable Settings → Privacy & Security → Developer Mode for this direct development installation. Developer Mode is not required for the later TestFlight installation.

Tap **Try demo**. Check the 20 × 10 × 5 cm object and export its shapefiles. Then try one real capture and verify the model, calibration, and share sheet. Resolve any Xcode compile or signing error before continuing. The capture/reconstruction runtime checks are authoritative about device support; a simulator can exercise the demo but cannot validate a real scan.

You can also run `bash Scripts/check_ios.sh` from the extracted project folder for an unsigned iOS compile check. That command checks compilation only; it does not create a TestFlight build.

**4. Create the app record in App Store Connect.**

Sign in at [App Store Connect](https://appstoreconnect.apple.com). Under **Apps → + → New App**, enter:

| Field | Value |
|---|---|
| Platform | iOS |
| Name | ShapeMeasure, or an available name you choose |
| Primary language | English |
| Bundle ID | The exact identifier from step 2 |
| SKU | A unique internal label, such as `shapemeasure-ios` |
| User access | Include the account that will perform internal testing |

Click Create. Creating a record requires the Account Holder, Admin, or App Manager role. If Apple blocks creation, the Account Holder should check for a pending developer agreement in App Store Connect's Business section.

**5. Archive and upload with Xcode.**

In Xcode, select the **ShapeMeasure scheme** and **Any iOS Device (arm64)** or the equivalent generic iOS device destination. Choose **Product → Archive**. The included shared scheme archives the Release configuration.

When Organizer opens, select the new archive. Validate it, then choose **Distribute App → App Store Connect → Upload** and complete Apple's signing/upload prompts. Wording can vary by Xcode release. Choose the regular App Store Connect upload route if you may later invite external testers; an upload marked **TestFlight Internal Only** cannot be used for external testing. Uploading a build does not by itself publish the app on the public App Store.

Alternatively, after signing in to Xcode, create the archive with this command from the project folder:

```bash
bash Scripts/archive_testflight.sh YOUR_TEAM_ID YOUR_BUNDLE_ID 1
```

Replace both placeholders with your actual 10-character Team ID and registered bundle ID. The Team ID appears in your [Apple Developer membership details](https://developer.apple.com/account). This helper uses automatic provisioning and creates `Builds/ShapeMeasure-1.xcarchive`. It opens the archive for the same Organizer upload steps; it does not upload automatically or change the version/build settings saved in the project. A failed archive can leave that path behind; use a new build number when retrying. Keep future GUI and script build numbers consistent.

Wait for Apple's processing to finish. The build appears under the app's TestFlight tab after processing; Apple sends a processing notification. If the upload fails, check the error shown by Organizer before starting another upload.

**6. Install on your own phone using an internal group.**

In App Store Connect, open the app → **TestFlight → Internal Testing → +**, create a group such as `Owner testing`, and add the processed build. Enter the What to Test text below if requested. Add your own eligible App Store Connect user as a tester; your enrolled Account Holder account can be used. Internal testers must have access to this app in App Store Connect.

Install [TestFlight from the iPhone App Store](https://apps.apple.com/app/testflight/id899247664). Open the invitation email on your iPhone, accept the invitation, and tap Install for ShapeMeasure. TestFlight builds expire after 90 days, so upload a newer build before continuing testing beyond that period.

**7. Invite people outside your developer team when ready.**

Create an External Testing group, add a regular processed build, complete the requested beta information, and submit it for TestFlight App Review. Apple reviews the first external build; enable invitations or a public link when the build is approved. Use your actual feedback email and review contact details. No app sign-in credentials are needed because ShapeMeasure has no account system.

Suggested beta description:

> ShapeMeasure reconstructs small rigid objects from guided iPhone captures, overlapping photos, or an orbit video. Inspect the 3D model, calibrate it against a known length, save point-to-point measurements, and export local-metric GIS shapefiles plus OBJ, JSON, and CSV. Guided scanning and on-device reconstruction require supported iPhone hardware and iOS 17 or later. This prototype's real-world measurement accuracy is still being evaluated.

Suggested What to Test:

> Start with Try demo and confirm the 20 × 10 × 5 cm box. Export and open the complete shapefile sets. On a supported Pro iPhone, scan a stationary textured object, inspect the model, compare it with a ruler, calibrate scale, save a distance, and export. Also test photo/video import, cancel/retry, reopening scans, and the share sheet. Report the phone model, iOS version, steps, and any error. Imported media requires known-length calibration.

Suggested review notes:

> No login or subscription is required. Try demo works without a scan and uses synthetic geometry. Guided capture and reconstruction require supported hardware; the app checks support. Real capture needs camera access. Keep the app open during reconstruction. The app stores scans locally, and users choose where to share exports. Shapefiles use local engineering coordinates in metres, not GPS coordinates. Measurements are estimates that need independent verification.

**If something blocks the upload or installation:**

| Message or symptom | Next action |
|---|---|
| Xcode cannot be installed on this Mac | Check Apple's Xcode compatibility table; install a compatible Xcode 26+ release or use another supported Mac. |
| Signing needs a development team or profile | Select the paid team, enable automatic signing, and finish Apple Account sign-in in Xcode. |
| Bundle ID unavailable or build appears under another app | Confirm the project and App Store Connect record use the identical registered identifier. |
| Archive action unavailable | Choose a generic physical iOS device destination and the ShapeMeasure scheme, rather than a simulator. |
| Version/build already uploaded | Increase General → Build, or the helper's final argument, and create another archive. |
| Build is processing | Wait for Apple's processing notification; check the displayed build status and any emailed errors. |
| Missing Compliance | Open Manage beside the build and complete Apple's encryption questions for the actual app you built. This source declares no non-exempt encryption and includes no custom cryptography or network backend; reassess the declaration if you add encrypted services or libraries. |
| Own account absent from internal testers | Check Users and Access, the user's eligible role, and access to this app. |
| Invitation has no installable build | Confirm processing finished and the build was added to that testing group; check its expiry. |
| Capture unavailable | Use the demo to test installation, then verify hardware/iOS support on the intended physical Pro iPhone. |

Apple references checked while preparing this package:

- [Upload build requirements](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds)
- [Xcode system requirements](https://developer.apple.com/xcode/system-requirements)
- [Xcode Mac App Store listing](https://apps.apple.com/us/app/xcode/id497799835?mt=12)
- [Create an app record](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app)
- [Internal TestFlight groups and testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers)
- [External TestFlight testing](https://developer.apple.com/help/app-store-connect/test-a-beta-version/invite-external-testers)
- [TestFlight overview and expiry](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview)
- [Declare encryption use](https://developer.apple.com/documentation/security/complying-with-encryption-export-regulations)
