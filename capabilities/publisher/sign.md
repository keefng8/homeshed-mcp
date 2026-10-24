# publisher.sign

HomeShed signs read-only library scripts from its vault (Homeshed-addon issue #58, publisher trust). It's the same
message and POST as the add-on's `homeshed-library publisher sign`, with the key kept in the vault instead of a file.

## Off until the owner turns it on
Nothing works until the owner makes a publisher key and stores it as `LIBRARY_PUBLISHER_KEY`:
1. On the PC: `homeshed-library publisher init` (prints the public key and fingerprint).
2. `python scripts/vault-put.py LIBRARY_PUBLISHER_KEY --from-file <the key file it names>`, then delete that file.
3. Add the public key in the library (Runs > Publisher keys) and trust it on each runner (tray Yes with Windows Hello).
Unsigned scripts keep working as before; no agent needs the key.

## Use
`publisher.sign(id, as_session="")`: the script's current body is fetched, its SHA-256 worked out here, and its risk
must be `read`. The request waits in the Control Panel's approvals bar with the code. Only the owner's Yes signs; if
the script changed since the request, nothing is signed. Uses the library address from Settings and
`HOMESHED_LIBRARY_OWNER` from the vault.

## Safety
The library never signs. A runner uses a signature only when it trusts this key, takes signed approvals, and its own
scan of the body finds nothing that writes.
