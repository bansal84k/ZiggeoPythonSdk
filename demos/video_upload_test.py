import os
import sys
import json
import time
import mimetypes
from pathlib import Path
from urllib.parse import urlencode

import requests

dir_path = os.path.dirname(os.path.realpath(__file__))
parent_dir_path = os.path.abspath(os.path.join(dir_path, os.pardir))
sys.path.insert(0, parent_dir_path)

from Ziggeo import Ziggeo


SESSION_COOKIE_PREFIX = "i07af2jp98rvoctt26y5egy3"

EMBED_API_BASE = "https://embed-api.ziggeo.com"
SESSION_BASE = "https://embed.ziggeo.com"


def usage():
    print("Error\n")
    print("Usage:")
    print("  python videos_create_server_auth_upload.py YOUR_APP_TOKEN YOUR_PRIVATE_KEY VIDEO_FILE [TTL_SECONDS]")
    print("")
    print("Example:")
    print(
        r'  python videos_create_server_auth_upload.py '
        r'441b70e2b704e0b05719766eac227c05 '
        r'YOUR_PRIVATE_KEY '
        r'"D:\Ziggeo\upload.mp4" '
        r'3600'
    )


def unwrap_ziggeo_response(data):
    """
    Handles Ziggeo _wrapstatus=true responses as well as plain JSON responses.
    """
    if isinstance(data, dict):
        if "result" in data:
            return data["result"]
        if "response" in data:
            return data["response"]
    return data


def parse_session_token(response):
    """
    The session endpoint may return plain text or JSON depending on environment.
    This handles both.
    """
    text = response.text.strip()

    try:
        data = response.json()
    except ValueError:
        return text.strip('"')

    data = unwrap_ziggeo_response(data)

    if isinstance(data, str):
        return data

    if isinstance(data, dict):
        for key in ("token", "session", "session_token", "value"):
            if data.get(key):
                return str(data[key])

    raise RuntimeError("Could not parse session token from response:\n" + text)


def query_session_token(app_token):
    """
    Queries the session token using the pattern you supplied:

      https://embed.ziggeo.com/v1/applications/[APP_TOKEN]/session
    """
    url = f"{SESSION_BASE}/v1/applications/{app_token}/session"

    response = requests.get(url, timeout=30)

    if not response.ok:
        raise RuntimeError(
            "Could not query Ziggeo session token.\n"
            f"HTTP {response.status_code}\n"
            f"URL: {url}\n"
            f"Response:\n{response.text}"
        )

    return parse_session_token(response)


def issue_upload_auth_token(ziggeo, ttl_seconds):
    """
    Creates the server-side auth token.

    Mirrors your PHP grant set:

      read   => all
      create => all
      update => session_owned
    """
    ttl_seconds = max(60, int(ttl_seconds))

    grants = {
        "read": {
            "all": True
        },
        "create": {
            "all": True
        },
        "update": {
            "session_owned": True
        }
    }

    arguments = {
        "expiration_date": int(time.time()) + ttl_seconds,
        "usage_expiration_time": ttl_seconds,
        "grants": json.dumps(grants)
    }

    response = ziggeo.authtokens().create(arguments)

    if not isinstance(response, dict) or not response.get("token"):
        raise RuntimeError(
            "Could not create server auth token. Ziggeo response:\n"
            + json.dumps(response, indent=2)
        )

    return str(response["token"]), arguments


def build_server_auth_query_string(app_token, server_auth_token, session_token):
    """
    This is the equivalent of your working client_auth example,
    except using server_auth instead of client_auth.

    Result:

      server_auth=<SERVER_AUTH_TOKEN>&i07af2jp98rvoctt26y5egy3<APP_TOKEN>=<SESSION_TOKEN>
    """
    session_key = SESSION_COOKIE_PREFIX + app_token

    params = {
        "server_auth": server_auth_token,
        session_key: session_token,
    }

    return urlencode(params)


def build_endpoint_url_with_server_auth(app_token, server_auth_query_string):
    """
    Builds:

      https://embed-api.ziggeo.com/v1/applications/<APP_TOKEN>/videos/videos-upload-url
        ?server_auth=<SERVER_AUTH_TOKEN>
        &i07af2jp98rvoctt26y5egy3<APP_TOKEN>=<SESSION_TOKEN>
        &_wrapstatus=true
        &_nocache=<timestamp>

    No intermediate_token is used.
    """
    endpoint = f"{EMBED_API_BASE}/v1/applications/{app_token}/videos/videos-upload-url"

    extra_params = urlencode({
        "_wrapstatus": "true",
        "_nocache": str(int(time.time() * 1000)),
    })

    return endpoint + "?" + server_auth_query_string + "&" + extra_params


def request_video_upload_url(app_token, video_file, server_auth_query_string):
    """
    Calls the browser-style upload URL flow:

      POST /v1/applications/<APP_TOKEN>/videos/videos-upload-url

    Auth is passed as the full server_auth query string.
    """
    url = build_endpoint_url_with_server_auth(
        app_token,
        server_auth_query_string,
    )

    payload = {
        "create_stream": "true",
        "video_file_name": Path(video_file).name,
    }

    response = requests.post(
        url,
        data=payload,
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            "Failed to create Ziggeo upload URL.\n"
            f"HTTP {response.status_code}\n"
            f"Request URL:\n{response.url}\n\n"
            f"Response:\n{response.text}"
        )

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Upload URL response was not JSON.\n"
            f"HTTP {response.status_code}\n"
            f"Response:\n{response.text}"
        )

    return unwrap_ziggeo_response(data)


def extract_url_data(upload_info):
    """
    Expected response usually includes:

      {
        "video": {...},
        "stream": {...},
        "url_data": {
          "url": "...",
          "fields": {...}
        }
      }
    """
    if not isinstance(upload_info, dict):
        raise RuntimeError(
            "Unexpected upload-url response:\n"
            + json.dumps(upload_info, indent=2)
        )

    url_data = upload_info.get("url_data")

    if not isinstance(url_data, dict) or not url_data.get("url"):
        raise RuntimeError(
            "Could not find url_data.url in upload-url response:\n"
            + json.dumps(upload_info, indent=2)
        )

    return url_data


def upload_file_to_signed_url(video_file, url_data):
    """
    Uploads the actual binary file to the signed URL returned by Ziggeo.
    """
    upload_url = url_data["url"]
    fields = url_data.get("fields") or {}

    mime_type = mimetypes.guess_type(video_file)[0] or "application/octet-stream"

    with open(video_file, "rb") as file_handle:
        files = {
            "file": (Path(video_file).name, file_handle, mime_type)
        }

        response = requests.post(
            upload_url,
            data=fields,
            files=files,
            timeout=300,
        )

    if response.status_code not in (200, 201, 204):
        raise RuntimeError(
            "Binary upload to signed URL failed.\n"
            f"HTTP {response.status_code}\n"
            f"Response:\n{response.text}"
        )

    return response


def extract_video_and_stream_tokens(upload_info):
    video = upload_info.get("video") if isinstance(upload_info.get("video"), dict) else {}
    stream = upload_info.get("stream") if isinstance(upload_info.get("stream"), dict) else {}

    video_token = (
        video.get("token")
        or upload_info.get("video_token")
        or upload_info.get("token")
    )

    stream_token = (
        stream.get("token")
        or upload_info.get("stream_token")
    )

    return video_token, stream_token


def build_confirm_url_with_server_auth(app_token, video_token, stream_token, server_auth_query_string):
    endpoint = (
        f"{EMBED_API_BASE}/v1/applications/{app_token}"
        f"/videos/{video_token}/streams/{stream_token}/confirm-video"
    )

    extra_params = urlencode({
        "_wrapstatus": "true",
        "_nocache": str(int(time.time() * 1000)),
    })

    return endpoint + "?" + server_auth_query_string + "&" + extra_params


def confirm_video_upload(app_token, video_token, stream_token, server_auth_query_string):
    """
    Confirms the uploaded stream.

    No intermediate_token is used here either.
    The same server_auth query string is reused.
    """
    url = build_confirm_url_with_server_auth(
        app_token,
        video_token,
        stream_token,
        server_auth_query_string,
    )

    response = requests.post(
        url,
        data={},
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            "Confirm video upload failed.\n"
            f"HTTP {response.status_code}\n"
            f"Request URL:\n{response.url}\n\n"
            f"Response:\n{response.text}"
        )

    try:
        return unwrap_ziggeo_response(response.json())
    except ValueError:
        return {
            "raw_response": response.text
        }


def main():
    if len(sys.argv) < 4:
        usage()
        sys.exit(1)

    app_token = sys.argv[1]
    private_key = sys.argv[2]
    video_file = sys.argv[3]
    ttl_seconds = int(sys.argv[4]) if len(sys.argv) >= 5 else 3600

    if not os.path.exists(video_file):
        raise RuntimeError("Video file does not exist: " + video_file)

    ziggeo = Ziggeo(app_token, private_key)

    server_auth_token, auth_arguments = issue_upload_auth_token(
        ziggeo,
        ttl_seconds,
    )

    session_token = query_session_token(app_token)

    server_auth_query_string = build_server_auth_query_string(
        app_token,
        server_auth_token,
        session_token,
    )

    print("Server auth token:")
    print(server_auth_token)
    print("")

    print("Session token:")
    print(session_token)
    print("")

    print("Full server auth query string:")
    print(server_auth_query_string)
    print("")

    print("Auth token creation arguments:")
    print(json.dumps(auth_arguments, indent=2))
    print("")

    upload_info = request_video_upload_url(
        app_token,
        video_file,
        server_auth_query_string,
    )

    print("videos-upload-url response:")
    print(json.dumps(upload_info, indent=2))
    print("")

    url_data = extract_url_data(upload_info)

    upload_file_to_signed_url(
        video_file,
        url_data,
    )

    print("Binary file uploaded to signed URL.")
    print("")

    video_token, stream_token = extract_video_and_stream_tokens(upload_info)

    if not video_token or not stream_token:
        print("Could not auto-detect video token or stream token from upload response.")
        print("Upload may have succeeded, but confirm-video was not called.")
        return

    confirm_response = confirm_video_upload(
        app_token,
        video_token,
        stream_token,
        server_auth_query_string,
    )

    print("confirm-video response:")
    print(json.dumps(confirm_response, indent=2))


if __name__ == "__main__":
    main()