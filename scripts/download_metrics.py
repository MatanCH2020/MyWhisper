"""Trigger an authenticated Studio snapshot from GitHub Actions (no shared secret)."""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def collect(endpoint, environment=os.environ, opener=None):
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("A public HTTPS Edge Function URL is required")
    if environment.get("GITHUB_REPOSITORY") != "MatanCH2020/MyWhisper":
        raise ValueError("Wrong repository")
    if environment.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Wrong branch")
    if environment.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch"):
        raise ValueError("Wrong event")
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    oidc_url = environment["ACTIONS_ID_TOKEN_REQUEST_URL"]
    oidc_host = urllib.parse.urlsplit(oidc_url)
    if oidc_host.scheme != "https" or not oidc_host.hostname or not (
            oidc_host.hostname.endswith(".actions.githubusercontent.com")
            or oidc_host.hostname.endswith(".actions.github.com")):
        raise ValueError("Invalid GitHub OIDC URL")
    joiner = "&" if "?" in oidc_url else "?"
    request = urllib.request.Request(oidc_url + joiner + "audience=mywhisper-download-sync",
        headers={"Authorization": "Bearer " + environment["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    with opener.open(request, timeout=20) as response:
        token = json.loads(response.read(65536)).get("value")
    if not isinstance(token, str) or len(token) < 20:
        raise ValueError("OIDC token unavailable")
    request = urllib.request.Request(endpoint, data=b"{}", method="POST",
        headers={"x-github-oidc-token": token, "Content-Type": "application/json"})
    with opener.open(request, timeout=90) as response:
        if response.status not in (200, 201):
            raise ValueError("Snapshot was not confirmed")
        result = json.loads(response.read(65536))
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("Snapshot was not confirmed")
    # Do not print tokens, responses, diagnostics or report content into CI logs.
    return result


if __name__ == "__main__":
    endpoint = os.environ.get("MYWHISPER_METRICS_URL", "")
    if not endpoint:
        print("MYWHISPER_METRICS_URL is not configured; collection did not run.", file=sys.stderr)
        raise SystemExit(1)
    try:
        collect(endpoint)
    except urllib.error.HTTPError as error:
        print(f"Download snapshot failed (HTTP {error.code}); previous samples remain unchanged.", file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print("Download snapshot failed; previous stored samples remain unchanged.", file=sys.stderr)
        raise SystemExit(1)
    print("Download snapshot committed successfully.")
