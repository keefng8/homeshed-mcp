"""email.*: send an email through the owner's own mail account, every send approved by the owner first."""

SETTINGS = {
    "email_smtp_host": {
        "type": "host", "default": "", "group": "Email",
        "label": "Outgoing mail server",
        "help": "Your provider's SMTP server, e.g. smtp.gmail.com (Gmail needs 2-step verification and an app "
                "password) or smtp.fastmail.com. Microsoft accounts are retiring password sign-in for this.",
    },
    "email_smtp_port": {
        "type": "select", "default": "587", "group": "Email", "options": ["587", "465"],
        "option_labels": {"587": "587 (STARTTLS, most providers)", "465": "465 (SSL)"},
        "label": "Port", "help": "587 unless your provider says 465.",
    },
    "email_from": {
        "type": "email", "default": "", "group": "Email",
        "label": "Send from (your email address)",
        "help": "The address emails come from. The password goes under Keys and passwords as SMTP_PASSWORD.",
    },
    "email_username": {
        "type": "email", "default": "", "group": "Email",
        "label": "Sign-in name (if not the address above)", "help": "Usually leave empty.",
    },
}
