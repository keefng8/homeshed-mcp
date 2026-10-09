# Image — Text check

## ID
`image.text_check`

## Purpose
Designs carry no text unless it's added as vector. A local design (2026-10-07, job 026d25e207b2) came out with a
garbled, signature-like mark in its bottom-right corner, and only a human eye caught it. This checks a design image
for words, pseudo-text and signature-like marks, free and local: RapidOCR (Apache-2.0, PaddleOCR's text detector and
reader on onnxruntime) on the image service's CPU, about 1-2 s for a 1024 px image. Nothing is saved.

## Parameters
Exactly one of:
| Name | Notes |
|---|---|
| `job_id` | A finished `image.generate` job, local or paid. |
| `image_name` | A saved image's name (`saved_as`); a paid one is read from the tool server's store. |
| `image_b64` | The image itself, up to 25 MB. |

## Returns
`{"ok", "text_found", "regions": [{"x", "y", "w", "h", "corner", "score", "read_as", "text", "pattern"?}],
"candidates", "size", "method", "ms"}`. `regions` lists every candidate the detector found (up to 20), the flagged ones
first; `corner` is `"br"`, `"bl"`, `"tr"`, `"tl"` or null. `"pattern": true` (only present when true) marks a drawn
repeat (see v2): it doesn't count toward `text_found`, but is worth a look.

## The rule (v2, 2026-10-08)
v2: a region whose reading, with spaces removed, is a single character repeated 3 or more times is a drawn pattern,
not text. 0, O and o count as the same character, and so do 1, l, I and |. Example: a chunky diamond knit band read
as "0000000" at 0.43. Real letters elsewhere in the image still fail it.

v1:
Drawn shapes (curly fur, snowflakes, stripes) also come back as candidates, but the reader makes them one stray
letter or a CJK character at low confidence; writing and pseudo-writing read as Latin letters. A region counts as
text when its reading has 2+ Latin letters or digits at confidence 0.30+, or, in a corner, where signatures sit,
1+ at 0.25+. Tuned on 5 real designs: the one with the corner mark was flagged ("aliE", 0.40, br); the other 4
passed. Treat a flag as "redo the design"; the candidate list lets a person check borderline ones.
