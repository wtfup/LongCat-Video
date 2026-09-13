"""W5 Publishers — YouTube Shorts adapter (dark v1).

Publish flow (YouTube Data API v3 resumable upload; executed only when the
dark gate passes at both layers):

  1. POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status
     -> session URI arrives in the Location response header (captured as
        ${upload_url})
  2. PUT  <upload_url>  with the rendered MP4 bytes (binary_put)

Title gets a #Shorts tag (kept <=100 chars). privacyStatus maps 1:1 from
visibility (public|unlisted|private). Zero network I/O in this module.
"""

from __future__ import annotations

from typing import Any, Mapping

from base import ChannelAdapter, PublishPlan, PublishRequest, PublishStep, render_caption

UPLOAD_HOST = "https://www.googleapis.com"
VISIBILITY_MAP = {"public": "public", "unlisted": "unlisted", "private": "private"}


def shorts_title(title: str) -> str:
    text = " ".join(str(title or "Untitled").split())
    suffix = "" if "#shorts" in text.lower() else " #Shorts"
    max_base = 100 - len(suffix)
    if len(text) > max_base:
        text = text[: max(max_base - 3, 0)].rstrip() + "..."
    return text + suffix


class YouTubeShortsAdapter(ChannelAdapter):
    channel = "youtube"
    credential_spec = {
        "YT_OAUTH_ACCESS_TOKEN": "OAuth 2.0 access token with youtube.upload scope (refresh at deploy)",
        "YT_CHANNEL_ID": "Target YouTube channel id (audit trail; upload binds to the token identity)",
    }
    auth_token_env = "YT_OAUTH_ACCESS_TOKEN"
    max_caption_chars = 4900

    def plan(self, request: PublishRequest) -> PublishPlan:
        caption = render_caption(request, limit=self.max_caption_chars)
        steps = (
            PublishStep(
                name="init_resumable",
                method="POST",
                url=UPLOAD_HOST + "/upload/youtube/v3/videos",
                query={"uploadType": "resumable", "part": "snippet,status"},
                json_body={
                    "snippet": {
                        "title": shorts_title(request.title),
                        "description": caption,
                        "channelId": "${YT_CHANNEL_ID}",
                    },
                    "status": {
                        "privacyStatus": VISIBILITY_MAP[request.visibility],
                        "selfDeclaredMadeForKids": False,
                    },
                },
                capture={"upload_url": "header:Location"},
            ),
            PublishStep(
                name="upload_bytes",
                method="PUT",
                url="${upload_url}",
                binary_file=request.video_path,
                headers={"Content-Type": "video/mp4"},
                capability="binary_put",
                capture={"post_id": "id"},
            ),
        )
        return PublishPlan(
            channel=self.channel,
            job_id=request.job_id,
            steps=steps,
            summary={
                "surface": "youtube_shorts",
                "privacy": VISIBILITY_MAP[request.visibility],
                "title_rendered": shorts_title(request.title),
            },
        )
