# Instagram — Publish

## ID
`instagram.publish`

## Purpose
Post one image, or a carousel of 2-10, with a caption to the owner's Instagram professional account (Business or
Creator), for a posting agent such as the Promoter (the owner, 2026-10-20: "auto-post to Instagram as well as
Threads"). **Public**, and it can't be unposted from here: call it only for a post that has been approved. Off until the
owner switches **Let apps post to Instagram** on (Settings > Shops and social), and only for clients granted it.

## Inputs
| Name | Required | Meaning |
|---|---|---|
| `text` | yes | The caption, 1-2,200 characters and at most 5 #hashtags, including the AI line. Links aren't clickable in Instagram captions: say "link in bio". |
| `ai_generated` | yes | `true` when any of it was made by AI: a last line "Image made with AI." is added (unless the caption already says "made with AI"). |
| `image_names` | yes | 1-10 images by their `saved_as` name: 1 = an image post, 2-10 = a carousel. JPEG only, 320-1440 px wide, 4:5 portrait to 1.91:1 landscape. Local images (image.carousel slides included) are sent as the image service's JPEG, up to 1440 px; a paid image is used as it is, so it must already be a JPEG. Every image is checked before anything is uploaded. |
| `alt_text` | no | A description read out for each image (screen readers), up to 1,000 characters. |

## How it posts
Meta's Instagram API with Instagram Login: each image is staged privately (a signed link, about an hour), a media
container is made from it (carousel items first, then the carousel), the tool waits until Meta says the container is
`FINISHED` (up to a minute), then calls `media_publish`. The staged files are deleted afterwards either way.

## Limits
At most `INSTAGRAM_DAILY_MAX` posts in 24 hours (default 25; Meta's own limit is 100). Each post is logged (who, when,
media type, ai_generated) and audited.

## Returns
`{"posted": true, "id", "permalink", "media_type": "IMAGE" | "CAROUSEL", "posted_today", "daily_max", "ai_generated",
"ai_line"}`.

## Errors
Switched off; not connected (connect in Settings > Shops and social); empty or too-long caption; more than 5
hashtags; no images or more than 10; an image that isn't a JPEG, is the wrong width or shape, or is over 8 MB; the
daily limit reached; Meta couldn't prepare the post. Tokens never appear in an answer.

## Connecting (the owner, once)
A Meta app with the **Instagram** product ("API setup with Instagram login"), its Instagram app ID and secret saved
under Credentials as `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET` (names can be changed in Settings), the Control
Panel's address added as the app's redirect URL, then **Connect Instagram**. The account must be Business or Creator.
