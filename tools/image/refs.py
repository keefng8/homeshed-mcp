"""One image's bytes from any of the places the tools name images (shared by etsy.listing.update's photos and
image.generate's edit mode, 2026-10-13):
- a Printify mockup address (Printify's image hosts only);
- "folder/name.png": saved in a folder of the image store under its own name (the image service's /folder-files/);
- an image the tool server keeps (mockup copies, paid providers' images);
- a generated image's name (the image service's /files/).
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

import httpx

from tools.commerce import _http as h

MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Etsy's photo host (a listing's url_fullxfull). Only for a read-only check such as image.compare: a tool that puts
# the image somewhere (a listing, a pin, an edit) never takes one, so another shop's photo can't be reused.
ETSY_PHOTO_HOST = "i.etsystatic.com"
FOLDER_REF = re.compile(r"^([A-Za-z0-9][A-Za-z0-9 _-]{0,39})/([A-Za-z0-9][A-Za-z0-9._-]{0,100}\.(?:png|jpg|jpeg))$")
NAME_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.(?:png|jpg|jpeg|webp)$")


def image_bytes(ref: str, max_bytes: int = MAX_IMAGE_BYTES, etsy_photos: bool = False) -> bytes:
    """The image's bytes, or a CommerceError saying why not. etsy_photos: also an Etsy listing photo's address."""
    from tools.image import providers as prov
    from tools._helper_auth import helper_headers
    from tools.image.generate import base_url
    from tools.printify.mockup import MOCKUP_HOSTS
    ref = (ref or "").strip()
    headers: dict = {}
    if ref.startswith("https://"):
        u = urlsplit(ref)
        hosts = (*MOCKUP_HOSTS, ETSY_PHOTO_HOST) if etsy_photos else MOCKUP_HOSTS
        if u.hostname not in hosts or u.username or u.password or len(ref) > 1000:
            raise h.CommerceError(f"{h.trim(ref, 80)}: only Printify mockup addresses (printify.product.get lists them)"
                                  + (" or Etsy listing photos (etsy.listing.get)" if etsy_photos else ""))
        url, headers = ref, {"User-Agent": "HomeShed-mcp-server"}
    elif m := FOLDER_REF.match(ref):
        url, headers = f"{base_url()}/image/folder-files/{quote(m[1])}/{m[2]}", helper_headers()
    elif NAME_REF.match(ref):
        kept = prov.output_dir() / ref
        if kept.is_file():
            if kept.stat().st_size > max_bytes:
                raise h.CommerceError(f"{ref} is over {max_bytes // 2**20} MB")
            return kept.read_bytes()
        url, headers = f"{base_url()}/image/files/{ref}", helper_headers()
    else:
        raise h.CommerceError(f'{h.trim(ref, 80)!r}: give an image name, "folder/name.png" or a Printify mockup address')
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=False, headers=headers) as r:
            if r.status_code == 404:
                raise h.CommerceError(f"there's no image {ref}")
            if r.status_code != 200:
                raise h.CommerceError(f"couldn't get {ref} (HTTP {r.status_code})")
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > max_bytes:
                    raise h.CommerceError(f"{ref} is over {max_bytes // 2**20} MB")
    except httpx.HTTPError as exc:
        raise h.CommerceError(f"couldn't get {ref} ({type(exc).__name__})") from None
    return data
