"""Configure this single-target project for a Codemagic build, before signing."""
from pathlib import Path
import argparse
import json
import re


def configure(project_path, bundle_id, counter, offset):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9][A-Za-z0-9-]*)+", bundle_id):
        raise ValueError("Use your own registered reverse-domain bundle identifier.")
    if bundle_id.lower().startswith(("com.example.", "com.yourcompany.")):
        raise ValueError("Replace both example bundle IDs in codemagic.yaml with your registered identifier.")
    if not re.fullmatch(r"[1-9][0-9]*", counter):
        raise ValueError("PROJECT_BUILD_NUMBER must be a positive integer supplied by Codemagic.")
    if not re.fullmatch(r"0|[1-9][0-9]*", offset):
        raise ValueError("BUILD_NUMBER_OFFSET must be a nonnegative integer.")
    build_number = int(counter) + int(offset)
    if build_number > 999999999:
        raise ValueError("Calculated build number is too large.")

    source = project_path.read_text(encoding="utf-8")
    # Both target configurations must be updated; fail before writing if the
    # project layout changed. Profile selection supplies the signing team next.
    replacements = {
        "PRODUCT_BUNDLE_IDENTIFIER": bundle_id,
        "CURRENT_PROJECT_VERSION": str(build_number),
    }
    for setting, value in replacements.items():
        source, count = re.subn(
            rf"(?<![A-Za-z0-9_]){setting}\s*=\s*[^;]*;",
            f"{setting} = {json.dumps(value)};",
            source,
        )
        if count != 2:
            raise ValueError(f"Expected Debug and Release {setting} settings; found {count}.")
    project_path.write_text(source, encoding="utf-8")
    return build_number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--counter", required=True)
    parser.add_argument("--offset", default="0")
    args = parser.parse_args()
    project_path = Path(__file__).resolve().parents[1] / "ShapeMeasure.xcodeproj/project.pbxproj"
    try:
        build_number = configure(project_path, args.bundle_id, args.counter, args.offset)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Build configuration failed: {error}\n")
    print(f"Configured {args.bundle_id}, build {build_number}.")


if __name__ == "__main__":
    main()
