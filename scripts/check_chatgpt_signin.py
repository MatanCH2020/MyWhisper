"""Owner-assisted sign-in diagnostic; never prints credentials or identity claims."""
import argparse
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from chatgpt_auth import AuthError, ChatGPTAuth, DEFAULT_MODEL
from cloud_http import CloudHTTP
from config import load_config, save_config
from external_browser import open_external_browser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="Open the selected browser for owner approval")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    http = CloudHTTP()
    auth = ChatGPTAuth(http)
    try:
        if args.connect:
            cfg = load_config()
            cfg["chatgpt_enabled"] = False
            if not save_config(cfg):
                raise AuthError("storage")
            print("Waiting for approval in the selected external browser.", flush=True)
            auth.sign_in(browser=lambda url: open_external_browser(url, cfg.get("chatgpt_browser", "system")))
            models = {m["slug"] for m in auth.status()["models"]}
            cfg["chatgpt_model"] = DEFAULT_MODEL if DEFAULT_MODEL in models else ""
            if not save_config(cfg):
                raise AuthError("storage")
        status = auth.status()
        print({key: status[key] for key in ("connected", "eligible", "enabled", "error", "sign_in_error")}, flush=True)
    except AuthError as error:
        print("Sign-in did not complete: " + str(error), flush=True)
        return 1
    finally:
        auth.close()
        http.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
