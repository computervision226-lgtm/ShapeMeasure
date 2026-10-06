# Deploy ShapeMeasure to TestFlight with Codemagic

Codemagic builds and signs the app on its cloud Mac, then uploads the signed IPA to App Store Connect. You can set up this route in a browser without installing Xcode on your own computer. Your enrolled Apple Developer Program account is still used for signing and TestFlight.

The included workflow uses Codemagic's documented Xcode 26.6 image. It runs the production geometry tests, configures this app's bundle ID and build number, applies your App Store provisioning profile, and builds the Release IPA. This source has not yet been compiled on an iOS build machine; the first cloud run will check the Swift code and framework linking.

**1. Put the extracted source in a Git repository.**

Unzip `ShapeMeasure_iPhone_Source.zip`. Upload or commit the **contents inside the ShapeMeasure folder** to the repository root. At that root you should have `codemagic.yaml`, `ShapeMeasure.xcodeproj`, `App`, `Core`, `Scripts`, and `Tests` together. This workflow builds the extracted project; a repository containing only the ZIP needs the source extracted first.

Open [Codemagic](https://codemagic.io), connect the Git provider, and add the repository. Use the YAML configuration for this native iOS app. Builds are started manually with the included configuration.

**2. Register the app with Apple.**

Choose a unique bundle identifier and register an explicit App ID in [Apple Developer → Identifiers](https://developer.apple.com/account/resources/identifiers/list). Then open [App Store Connect](https://appstoreconnect.apple.com) → Apps → + → New App. Select iOS, enter an available app name and primary language, choose that exact Bundle ID, enter a unique SKU such as `shapemeasure-ios`, and create the record.

Keep the same bundle ID for the app record, the provisioning profile, and both bundle-ID fields in the YAML. The source project's bundle ID is configured during the cloud build.

**3. Connect your Apple API key to Codemagic.**

In App Store Connect → Users and Access → Integrations → App Store Connect API, create a **Team key with App Manager access**. Download its `.p8` private key and note its Issuer ID and Key ID. If API access needs activation, the Account Holder completes that first.

In Codemagic Team settings → Team integrations → Developer Portal → Manage keys, add the key. Name the integration **`shapemeasure-apple`**, enter the IDs, and upload the `.p8` directly there. If you already have a suitable integration, use its exact name in `integrations.app_store_connect` in the YAML.

**4. Add the distribution certificate and provisioning profile.**

Open Codemagic Team settings → codemagic.yaml settings → Code signing identities.

- Under iOS certificates, use an existing matching Apple Distribution certificate that Codemagic holds, or choose Generate certificate → Apple Distribution with your API integration. For a new certificate, download it and then upload the resulting `.p12` with its password into Code signing identities. Follow the UI's instructions to retain its private key.
- In the Apple Developer portal, create an **App Store Connect distribution provisioning profile** for your explicit App ID, selecting that Apple Distribution certificate. Download the `.mobileprovision` file.
- Under Codemagic's iOS provisioning profiles, upload the profile or use Fetch profiles to retrieve it. Confirm the displayed bundle ID and matching certificate.

If Apple's distribution-certificate limit is reached, use an existing certificate with its matching private key. A public certificate alone cannot sign the build.

**5. Set the YAML values.**

Edit `codemagic.yaml` before starting the build:

| Setting | Your value |
|---|---|
| `integrations.app_store_connect` | Your Codemagic integration name; default `shapemeasure-apple` |
| `environment.ios_signing.bundle_identifier` | Your registered bundle ID |
| `environment.vars.BUNDLE_ID` | The same registered bundle ID |
| `environment.vars.BUILD_NUMBER_OFFSET` | Keep `"0"` for a new app; see build numbering below |

Replace **both** occurrences of `com.yourcompany.shapemeasure`. Keep `distribution_type: app_store`, Xcode `"26.6"`, project `ShapeMeasure.xcodeproj`, and scheme `ShapeMeasure` as provided. No CocoaPods or Swift dependency installation is needed.

The build number is **Codemagic's PROJECT_BUILD_NUMBER + BUILD_NUMBER_OFFSET**. The project counter increases across this Codemagic application's workflows. If the same Apple app/version already has uploaded builds, choose an offset so the next calculated number exceeds them. For example, counter 3 plus offset 100 gives build 103. Increase the offset if moving to a new Codemagic application or another build system with a different counter. This workflow does not query Apple's latest build number.

**6. Start the cloud build and install through TestFlight.**

In Codemagic, choose Start new build, select the branch containing the updated source, and choose **ShapeMeasure TestFlight**. A successful run produces a signed IPA under the build artifacts and uploads it using your Apple integration.

Wait for Apple to process the upload. In App Store Connect, open the app → TestFlight → Internal Testing → +. Create a group, add the processed build and your own eligible App Store Connect account. Install [TestFlight on your iPhone](https://apps.apple.com/app/testflight/id899247664), accept the invitation, and install ShapeMeasure.

The workflow uploads the IPA; initial internal-group assignment is completed in App Store Connect. TestFlight builds expire after 90 days. Later cloud builds can be added to the same group. For external testers, complete the beta information and submit a regular build for Apple's TestFlight review; the suggested description and testing notes are in `TESTFLIGHT.md`.

After installation, tap **Try demo** and confirm the 20 × 10 × 5 cm box. Then try a real scan, ruler calibration, saved distance, and export on your supported Pro iPhone. Capture/reconstruction hardware checks run on the phone. Cloud compilation cannot verify measurement accuracy or real-camera behavior.

**If the build stops:**

| Failure | Check |
|---|---|
| YAML or project not found | The extracted project contents and `codemagic.yaml` must be at the Git repository root. |
| Integration name not found | Use the exact Codemagic Developer Portal key name and an App Manager Team API key. |
| No matching profiles or certificates | Confirm the bundle ID in both YAML fields, the App Store distribution profile, certificate/private key, and expiry. |
| Bundle-ID placeholder rejected | Replace both example IDs; commit the changes and build the updated branch. |
| Swift compiler error or Xcode exit code 65 | Open the build log and find the first compiler/signing error. Share that error and surrounding lines so the source can be corrected. |
| Duplicate build number | Increase BUILD_NUMBER_OFFSET to exceed the existing uploaded build number, then start a new build. |
| Upload succeeded but no installable build | Check Apple processing/compliance status, then confirm the processed build is assigned to the internal group. |

Official references used for this configuration:

- [Codemagic native iOS workflow](https://docs.codemagic.io/yaml-quick-start/building-a-native-ios-app/)
- [Codemagic iOS signing](https://docs.codemagic.io/yaml-code-signing/signing-ios/)
- [Codemagic first signed build](https://docs.codemagic.io/yaml-quick-start/first-signed-build/)
- [Codemagic App Store Connect upload](https://docs.codemagic.io/yaml-publishing/app-store-connect/)
- [Codemagic Xcode 26.6 image](https://docs.codemagic.io/specs-macos/xcode-26-6/)
- [Codemagic build counters](https://docs.codemagic.io/knowledge-codemagic/build-versioning/)
- [Apple API key setup](https://developer.apple.com/help/app-store-connect/get-started/app-store-connect-api/)
- [Apple internal TestFlight testing](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers)
