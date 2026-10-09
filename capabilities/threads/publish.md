# Threads — Publish

## ID
`threads.publish`

## Purpose
Post text, one image or a carousel to the Threads account the owner connected (Settings > Online shops > Connect
Threads). **Public.** Only call it for a post that has been approved for publishing. Off until the owner switches
"Let apps post to Threads" on.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `text` | yes | 1-500 characters (emoji count as their UTF-8 bytes, as Meta counts them). |
| `link` | no | One `https://` address, shown as a preview card. Text posts only (Meta ignores it with images). |
| `image_names` | no | Images by their `saved_as` name: 1 = an IMAGE post, 2-10 = a CAROUSEL. |
| `alt_text` | no | A description of the images for screen readers (each image gets it), up to 1000 characters. |
| `video_name` | no | A finished Studio video by name (no `.mp4`): a VIDEO post, up to 5 minutes and 1 GB, staged in R2 like images; Meta gets up to 5 minutes to process it. Not with `image_names`. |
| `topic_tag` | no | The post's one topic (blue, links to the topic's feed): 1-50 characters, spaces allowed, no `.` or `&` (Meta's rules); a leading `#` is dropped. Sent on the post's own container, so the text needs no #tag. |

## Media (2026-10-07)
Meta only takes media from a public URL. Each image is checked first (JPEG or PNG, up to 8 MB, 320-1440 px wide;
nothing is uploaded or posted if one fails), then staged in the private R2 bucket (`r2.py`; vault entries
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_CLOUDFLARE_S3`; bucket `R2_BUCKET`, default `homeshed-studio`) with a
presigned link that works for an hour, and deleted again once the post is published or fails. Local images go as the
image service's JPEG fitted in 1440 px (its originals can be 20 MB upscales); paid images (tool server) go as they are.

## Returns
`{"posted": true, "id", "permalink", "media_type", "posted_today", "daily_max"}`.

## Limits
At most `THREADS_DAILY_MAX` posts in 24 hours (default 25; Meta's own limit is 250). Each post is a container (one per
image, plus the carousel), up to 30 s of waiting for each to finish as Meta recommends, then publish. Every post is in
the client audit trail.

## Errors
The switch is off, Threads isn't connected, the text is empty or too long, an image is missing, the wrong type, too
big or the wrong width, R2 isn't set up, the daily limit is reached, or Meta refused (its reason is passed on, never
the token or a media link).
