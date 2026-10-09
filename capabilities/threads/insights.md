# Threads — Insights

## ID
`threads.insights`

## Purpose
How one of the owner's posts is doing, so a posting agent can learn which formats, hooks and timings work. Read-only.

## Parameters
| Name | Required | Notes |
|---|---|---|
| `post_id` | no | The id `threads.publish` returned (digits only). Empty = the account's follower count. |

## Returns
`{"post_id", "metrics": {"views", "likes", "replies", "reposts", "quotes", "shares"}}`. A metric Meta didn't return is
`null`. With no `post_id`: `{"account": true, "followers", "at"}` (UTC); read it before and after a post to estimate
followers gained. Meta's numbers can lag a little; read them after about 24 hours and again after 7 days.

## Needs
The `threads_manage_insights` permission (added to the connect scopes 2026-10-07). A connection made before that
doesn't have it: connect again in Settings > Online shops, and add the permission to the app's Threads use case in
Meta's developer site if it isn't offered.

## Errors
Not connected, a bad post id, the permission missing (says how to fix it), or Meta refused (its reason, never the
token).
