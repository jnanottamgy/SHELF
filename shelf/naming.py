"""What a stored file is called when it leaves us.

One definition, because there are now two exits -- a WhatsApp document and a
browser download -- and a student who saves the same file from both should get
the same name twice. A second copy of this logic would drift within a month,
and the way it drifts is invisible: the file still arrives, just named
something the student did not expect.
"""

from pathlib import PurePosixPath

from shelf.models import Resource

#: WhatsApp and most filesystems cope well past this; a name longer than this
#: stops being readable in a file list, which is the thing that matters.
MAX_NAME = 64


def filename_for(resource: Resource) -> str:
    """What the student sees when the file lands.

    The name it arrived with when we have one -- that is what they recognise.
    Otherwise their own title, with the extension recovered from the storage
    key: WhatsApp shows the name, and "notes" without a suffix opens in nothing.

    The suffix is stripped before truncating and put back after, because
    trimming a long name that already ended in ".pdf" used to cut the ".pdf"
    off and hand over a file nothing would open.
    """
    name = resource.filename or (resource.title or "file")
    stem = name.strip() or "file"
    # Prefer the original name's own extension; fall back to the storage key's.
    suffix = PurePosixPath(stem).suffix or (
        PurePosixPath(resource.storage_ref).suffix if resource.storage_ref else ""
    )
    if suffix and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    return f"{stem[: MAX_NAME - len(suffix)].strip() or 'file'}{suffix}"
