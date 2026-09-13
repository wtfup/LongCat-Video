"""W5 Publishers — Instagram Reels adapter (dark v1).

Publish flow (Instagram Graph API shape; executed only when the dark gate
passes at both layers):

  1. POST /{graph-version}/{ig-user-id}/media          -> create REELS container
  2. GET  /{graph-version}/{container-id}              -> poll status_code == FINISHED
  3. POST /{graph-version}/{ig-user-id}/media_publish  -> publish container

Notes:

* ``video_url`` must be a public HTTPS URL at live time (the factory hosts
  renders on S3/CloudFront). When ``extra["video_url"]`` is absent the local
  ``video_path`` renders into the plan verbatim as a deploy-time reminder.
* Reels supports public visibility only in this adapter.
* Zero network I/O; this module only builds plans.
"""

from __future__ import annotations

from typing import Any, Mapping

from base import ChannelAdapter, PublishPlan, PublishRequest, PublishStep, PublisherError, render_caption

GRAPH_HOST = "https://graph.facebook.com"
GRAPH_VERSION = "v21.0"


class InstagramGraphAdapter(ChannelAdapter):
    channel = "instagram"
    credential_spec = {
        "IG_GRAPH_ACCESS_TOKEN": "Long-lived Instagram Graph API access token (instagram_content_publish)",
        "IG_USER_ID": "Instagram Business account id used as {ig-user-id} in graph paths",
    }
    auth_token_env = "IG_GRAPH_ACCESS_TOKEN"
    max_caption_chars = 2200

    def plan(self, request: PublishRequest) -> PublishPlan:
        if request.visibility != "public":
            raise PublisherError("instagram: only visibility='public' is supported by this adapter")
        video_ref = str(request.extra.get("video_url") or request.video_path)
        caption = render_caption(request, limit=self.max_caption_chars)
        steps = (
            PublishStep(
                name="create_container",
                method="POST",
                url=GRAPH_HOST + "/" + GRAPH_VERSION + "/${IG_USER_ID}/media",
                json_body={
                    "media_type": "REELS",
                    "video_url": video_ref,
                    "caption": caption,
                    "share_to_feed": True,
                },
                capture={"container_id": "id"},
            ),
            PublishStep(
                name="poll_container",
                method="GET",
                url=GRAPH_HOST + "/" + GRAPH_VERSION + "/${container_id}",
                query={"fields": "status_code,status"},
                poll={"field": "status_code", "until": "FINISHED", "max_attempts": 30, "interval_s": 10.0},
            ),
            PublishStep(
                name="publish_container",
                method="POST",
                url=GRAPH_HOST + "/" + GRAPH_VERSION + "/${IG_USER_ID}/media_publish",
                json_body={"creation_id": "${container_id}"},
                capture={"post_id": "id"},
            ),
        )
        return PublishPlan(
            channel=self.channel,
            job_id=request.job_id,
            steps=steps,
            summary={
                "surface": "instagram_reels",
                "graph_version": GRAPH_VERSION,
                "hosting": "video_url" if request.extra.get("video_url") else "video_path_passthrough",
                "caption_chars": len(caption),
            },
        )
