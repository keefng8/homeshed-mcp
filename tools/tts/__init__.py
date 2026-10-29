"""Text to speech on Google Cloud, Chirp 3 HD voices (tts.*; to-do #151, 2026-10-28). See ../../capabilities/tts/synthesize.md.

The owner's books become audiobooks: an app (the station) cuts a book into pieces under Google's per-request limit,
calls tts.synthesize for each, takes the MP3s with tts.fetch and joins them. These tools hold the key, keep the files
and count the characters, so the month stays inside Google's free allowance unless the owner allows more.

SETTINGS joins runtime_settings (the Settings page draws it), the same way the image provider switches do.
"""

PAID_OPTIONS = ["0", "100000", "250000", "500000", "1000000", "2000000", "5000000"]

SETTINGS = {
    "tts_paid_chars_month": {
        "type": "select", "default": "0", "group": "Text to speech",
        "options": PAID_OPTIONS,
        "option_labels": {o: ("None: free allowance only" if o == "0" else
                              f"{int(o):,} more characters (about ${int(o) * 30 / 1_000_000:.0f})")
                          for o in PAID_OPTIONS},
        "label": "Google text to speech: paid characters a month",
        "help": "Google gives 1 million Chirp 3 HD characters a month free, then charges $30 per million. With None, "
                "tts.synthesize stops at the free million. A choice here lets it go that many characters further each "
                "month, charged to your Google Cloud billing account. The count is this server's own: other use of the "
                "same Google account isn't seen.",
    },
}
