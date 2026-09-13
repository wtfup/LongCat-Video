"""W5 Publishers — Facebook Page adapter (dark v1).

Publish flow (Facebook Graph API shape; executed only when the dark gate
passes at both layers):

  1. POST /{graph-version}/{page-id}/videos   -> publish (or draft) the video

``file_url`` must be a public HTTPS URL at live time (S3/CloudFront render
host). When ``extra["video_url"]`` is absent the local ``video_path`` renders
into the plan verbatim as a deploy-time reminder. ``published`` follows
visibility: public -> true; unlisted/private -> false (FB page draft).

Zero network I/O; this module only builds plans.
"""

from __future__ import annotations

from typing import Any, Mapping

from base import ChannelAdapter, PublishPlan, PublishRequest, PublishStep, render_caption

GRAPH_HOST = "https://graph.facebook.com"
GRAPH_VERSION = "v21.0"


class FacebookPageAdapter(ChannelAdapter):
    channel = "facebook"
    credential_spec = {
        "FB_PAGE_ACCESS_TOKEN": "Facebook Page access token (pages_manage_posts, pages_read_engagement)",
        "FB_PAGE_ID": "Facebook Page id used as {page-id} in graph paths",
    }
    auth_token_env = "FB_PAGE_ACCESS_TOKEN"
    max_caption_chars = 5000

    def plan(self, request: PublishRequest) -> PublishPlan:
        video_ref = str(request.extra.get("video_url") or request.video_path)
        published = request.visibility == "public"
        caption = render_caption(request, limit=self.max_caption_chars)
        steps = (
            PublishStep(
                name="publish_video",
                method="POST",
                url=GRAPH_HOST + "/" + GRAPH_VERSION + "/${FB_PAGE_ID}/videos",
                json_body={
                    "file_url": video_ref,
                    "description": caption,
                    "title": request.title,
                    "published": published,
                },
                capture={"post_id": "id"},
            ),
        )
        return PublishPlan(
            channel=self.channel,
            job_id=request.job_id,
            steps=steps,
            summary={
                "surface": "facebook_page_video",
                "graph_version": GRAPH_VERSION,
                "published": published,
                "hosting": "video_url" if request.extra.get("video_url") else "video_path_passthrough",
            },
        )
