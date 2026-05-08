import os
import sys
import json
import time
from urllib.parse import urlencode

import requests

dir_path = os.path.dirname(os.path.realpath(__file__))
parent_dir_path = os.path.abspath(os.path.join(dir_path, os.pardir))
sys.path.insert(0, parent_dir_path)

from Ziggeo import Ziggeo


SESSION_COOKIE_PREFIX = "i07af2jp98rvoctt26y5egy3"

VIDEO_CDN_BASE = "https://video-cdn.ziggeo.com"
EMBED_API_BASE = "https://embed-api.ziggeo.com"


DEFAULT_APP_TOKEN = "441b70e2b704e0b05719766eac227c05"
DEFAULT_VIDEO_TOKEN = "67bfeb35f16f42a60db1c3568ef048ef"


def usage():
    print("Usage:")
    print("  python video_playable_link.py YOUR_APP_TOKEN YOUR_PRIVATE_KEY YOUR_VIDEO_TOKEN [TTL_SECONDS]")
    print("")
    print("Example:")
    print(
        "  python video_playable_link.py "
        "441b70e2b704e0b05719766eac227c05 "
        "YOUR_PRIVATE_KEY "
        "67bfeb35f16f42a60db1c3568ef048ef "
        "3600"
    )
    print("")
    print("Or set:")
    print("  ZIGGEO_APP_TOKEN")
    print("  ZIGGEO_PRIVATE_KEY")
    print("  ZIGGEO_VIDEO_TOKEN")
    print("  ZIGGEO_AUTH_TTL_SECONDS")


def unwrap_ziggeo_response(data):
    """
    Handles Ziggeo wrapped responses.

    Possible shapes:
      {"status": "200", "responseText": {"token": "..."}}
      {"status": 200, "response": {"token": "..."}}
      {"result": {"token": "..."}}
      {"token": "..."}
    """
    if isinstance(data, dict):
        if "responseText" in data:
            return data["responseText"]

        if "response" in data:
            return data["response"]

        if "result" in data:
            return data["result"]

    return data


def parse_session_token(response):
    """
    Parses the session token from Ziggeo's session endpoint.
    """
    text = response.text.strip()

    try:
        data = response.json()
    except ValueError:
        return text.strip('"')

    if isinstance(data, dict):
        status = data.get("status", 200)

        try:
            status_int = int(status)
        except Exception:
            status_int = 200

        if status_int >= 400:
            raise RuntimeError(
                "Session request failed.\n"
                f"HTTP wrapper status: {status}\n"
                f"Response:\n{json.dumps(data, indent=2)}"
            )

    data = unwrap_ziggeo_response(data)

    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        for key in ("token", "session", "session_token", "value"):
            value = data.get(key)
            if value:
                return str(value)

    raise RuntimeError("Could not parse session token from response:\n" + text)


def query_session_token(app_token):
    """
    Uses the same embed-api session pattern as the working upload flow.
    """
    url = f"{EMBED_API_BASE}/v1/applications/{app_token}/session"

    response = requests.post(
        url,
        params={
            "noauth": "false",
            "_wrapstatus": "true",
            "_nocache": str(int(time.time() * 1000)),
        },
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            "Could not query Ziggeo session token.\n"
            f"HTTP {response.status_code}\n"
            f"URL: {response.url}\n"
            f"Response:\n{response.text}"
        )

    return parse_session_token(response)


def issue_playback_auth_token(ziggeo, video_token, ttl_seconds):
    """
    Creates a direct-use server auth token for playback.

    Important:
      - Do NOT set hidden=true.
      - hidden=true tokens cannot be used directly as server_auth.
      - For playback, only read permission is needed.

    Option A below restricts the token to one specific video.
    If this does not work with your app's auth setup, use Option B.
    """
    ttl_seconds = max(60, int(ttl_seconds))

    # Option A: tighter token, only for this video.
    grants = {
        "read": {
            "resources": [video_token]
        }
    }

    # Option B: broader read token.
    # Uncomment this if your playback URL rejects the resource-scoped token.
    #
    # grants = {
    #     "read": {
    #         "all": True
    #     }
    # }

    arguments = {
        "expiration_date": int(time.time()) + ttl_seconds,
        "usage_expiration_time": ttl_seconds,
        "grants": json.dumps(grants),
        "volatile": False,

        # Do NOT include this for direct server_auth usage:
        # "hidden": "true",
    }

    response = ziggeo.authtokens().create(arguments)

    if not isinstance(response, dict) or not response.get("token"):
        raise RuntimeError(
            "Could not create server auth token. Ziggeo response:\n"
            + json.dumps(response, indent=2)
        )

    return str(response["token"]), arguments


def build_playable_video_url(app_token, video_token, server_auth_token, session_token):
    """
    Final shape:

      https://video-cdn.ziggeo.com/v1/applications/<APP_TOKEN>/videos/<VIDEO_TOKEN>/video.mp4
        ?server_auth=<SERVER_AUTH_TOKEN>
        &i07af2jp98rvoctt26y5egy3<APP_TOKEN>=<SESSION_TOKEN>
    """
    session_key = SESSION_COOKIE_PREFIX + app_token

    query_string = urlencode({
        "server_auth": server_auth_token,
        session_key: session_token,
    })

    return (
        f"{VIDEO_CDN_BASE}/v1/applications/{app_token}"
        f"/videos/{video_token}/video.mp4"
        f"?{query_string}"
    )


def check_playable_url(playable_url):
    """
    Optional sanity check.

    Some CDNs may not support HEAD consistently, so this uses a tiny ranged GET.
    """
    response = requests.get(
        playable_url,
        headers={
            "Range": "bytes=0-1"
        },
        timeout=30,
        stream=True,
    )

    return {
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "content_length": response.headers.get("Content-Length"),
        "accept_ranges": response.headers.get("Accept-Ranges"),
    }


def main():
    if len(sys.argv) >= 4:
        app_token = sys.argv[1]
        private_key = sys.argv[2]
        video_token = sys.argv[3]
        ttl_seconds = int(sys.argv[4]) if len(sys.argv) >= 5 else 3600
    else:
        app_token = os.getenv("ZIGGEO_APP_TOKEN", DEFAULT_APP_TOKEN)
        private_key = os.getenv("ZIGGEO_PRIVATE_KEY")
        video_token = os.getenv("ZIGGEO_VIDEO_TOKEN", DEFAULT_VIDEO_TOKEN)
        ttl_seconds = int(os.getenv("ZIGGEO_AUTH_TTL_SECONDS", "3600"))

        if not private_key:
            usage()
            raise RuntimeError(
                "Missing private key. Pass it as the second command-line argument "
                "or set ZIGGEO_PRIVATE_KEY."
            )

    ziggeo = Ziggeo(app_token, private_key)

    server_auth_token, auth_arguments = issue_playback_auth_token(
        ziggeo,
        video_token,
        ttl_seconds,
    )

    session_token = query_session_token(app_token)

    playable_url = build_playable_video_url(
        app_token,
        video_token,
        server_auth_token,
        session_token,
    )

    print("Server auth token:")
    print(server_auth_token)
    print("")

    print("Session token:")
    print(session_token)
    print("")

    print("Auth token creation arguments:")
    print(json.dumps(auth_arguments, indent=2))
    print("")

    print("Playable video URL:")
    print(playable_url)
    print("")

    # Optional check. Comment this block if you only want to print the URL.
    try:
        check = check_playable_url(playable_url)
        print("Playback URL check:")
        print(json.dumps(check, indent=2))
    except Exception as exc:
        print("Playback URL was generated, but the optional check failed:")
        print(str(exc))


if __name__ == "__main__":
    main()