# image.delete

Delete one image the tool server keeps, now instead of waiting for a sweep. The main use: a site deletes a buyer's
upload the moment the design made from it is ready, and the paid design when the listing comes down.

## Input
- `name`: `image.store`'s `name`, or a paid `image.generate` job's `saved_as`.

## Returns
`{"deleted": name, "sidecar_deleted": bool}`. A paid job's `.json` record (prompt, inputs) goes with its image.

## Who may delete what
- An app: only its own. An upload's name carries the app that stored it (`upload-<app>-...`); a paid image's record
  says which app asked for it.
- The owner token: any image kept here.
- Refused: a name that isn't an image name, one that isn't kept here (the error starts "not found:"), or someone
  else's (it starts "not yours:"). Both are final, so don't retry them.

## Not covered
Images on the local image service (local `image.generate` results, `image.print_file` output) live there, not here;
this tool doesn't delete them.

## Access
`risk: write`, so an app needs `image.delete` granted in Control Panel > API access.

## Implementation
`tools/image/delete.py`.

## Machine-readable definition
`delete.json`
