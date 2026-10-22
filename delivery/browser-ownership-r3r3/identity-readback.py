"""Read-only fresh official HTTPS builder/AMO/XPI identity; no browser or install."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
import zipfile

GUID = "vpn@proton.ch"
COMMIT = "71d7d86f164a2ee8e50ce5a389c9e5cbe0fd6883"
BLOBS = {"webpack.config.js": "0f37f18649a35b360e68a6bb242af6a8bfbae5ac",
         "webpack-ff.config.js": "270d4105197117232547511fbbf8aa6ad3a82af2"}
AMO = "https://addons.mozilla.org/api/v5/addons/addon/proton-vpn-firefox-extension/"


def official_https(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (parts.scheme != "https" or parts.username or parts.password
            or not (host in ("raw.githubusercontent.com", "addons.mozilla.org")
                    or host.endswith(".mozilla.net"))):
        raise ValueError("NON_OFFICIAL_HTTPS_ROUTE")


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        official_https(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(url, limit):
    official_https(url)
    with build_opener(OfficialRedirects()).open(url, timeout=15) as response:
        official_https(response.url)
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("RESPONSE_TOO_LARGE")
    return data


def xpi_identity(amo, data):
    file = amo["current_version"]["file"]
    digest = hashlib.sha256(data).hexdigest()
    if amo["guid"] != GUID or file["hash"] != "sha256:" + digest:
        raise ValueError("AMO_GUID_OR_XPI_DIGEST_MISMATCH")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo("manifest.json")
        if info.file_size > 1_000_000:
            raise ValueError("MANIFEST_TOO_LARGE")
        manifest = json.loads(archive.read(info))
        names = {name.lower() for name in archive.namelist()}
        signed = {"meta-inf/mozilla.rsa", "meta-inf/mozilla.sf", "meta-inf/manifest.mf"} <= names
    gecko = manifest.get("browser_specific_settings", manifest.get("applications", {})).get("gecko", {})
    if gecko.get("id") != GUID or manifest["version"] != amo["current_version"]["version"]:
        raise ValueError("CURRENT_XPI_ID_OR_VERSION_MISMATCH")
    if not signed:
        raise ValueError("SIGNATURE_CONTAINER_ABSENT")
    return {"guid": GUID, "version": manifest["version"], "sha256": digest,
            "signatureContainerPresent": True, "cryptographicSignatureVerified": False,
            "browserSignatureVerification": "REQUIRED_ON_ATTENDED_INSTALL"}


def readback():
    builders = []
    for name, expected in BLOBS.items():
        data = fetch(f"https://raw.githubusercontent.com/ProtonVPN/proton-vpn-browser-extension/{COMMIT}/{name}", 1_000_000)
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != expected:
            raise ValueError("PINNED_BUILDER_BLOB_MISMATCH")
        builders.append({"path": name, "gitBlobSha1": blob,
                         "sha256": hashlib.sha256(data).hexdigest()})
    amo_bytes = fetch(AMO, 2_000_000)
    amo = json.loads(amo_bytes)
    package = fetch(amo["current_version"]["file"]["url"], 20_000_000)
    return {"status": "FRESH_PACKAGE_IDENTITY_OBSERVED_SIGNING_PENDING",
            "observedAtUtc": datetime.now(timezone.utc).isoformat(),
            "upstreamCommit": COMMIT, "builders": builders,
            "amoResponseSha256": hashlib.sha256(amo_bytes).hexdigest(),
            "xpi": xpi_identity(amo, package), "installed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = readback()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        result = {"status": "IDENTITY_READBACK_HELD", "errorType": type(error).__name__,
                  "installed": False, "cryptographicSignatureVerified": False}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "installed": False}))
    return 0 if "xpi" in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
