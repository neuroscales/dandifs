# dandifs

An [`fsspec`](https://filesystem-spec.readthedocs.io) filesystem for the
[DANDI archive](https://dandiarchive.org).

`dandifs` resolves `dandi://` URLs to the bytes behind DANDI assets, including
files that live **inside a Zarr asset**. It is async-first (built on
`aiohttp`, via fsspec's `AsyncFileSystem`), has a small dependency footprint,
and does **not** depend on `dandi` or `dandi-schema`.

## Install

```bash
pip install dandifs
# optional: keyring-based credentials for private/embargoed dandisets
pip install "dandifs[auth]"
```

## URL format

```
dandi://<instance>/<dandiset>[@<version>]/<path>
```

- `<instance>` — a registered instance name (`dandi`, `dandi-staging`,
  `ember`, `ember-sandbox`, ...) or any DANDI-schema server (see *Instances*).
- `<dandiset>` — the six-digit dandiset identifier, e.g. `000026`.
- `@<version>` — optional; defaults to the most recent published version, or
  the draft version if none is published.
- `<path>` — a path within the dandiset. It may descend **into** a Zarr asset:
  in `.../image.zarr/0/0/0`, only `.../image.zarr` is a registered asset and
  `0/0/0` is a key inside that Zarr's store.

## Public surface

The **only** public API is the filesystem class, `DandiFileSystem` (with the
alias `RemoteDandiFileSystem`). Everything else is internal.

## Usage

### Via the registered protocol

```python
import fsspec, json

with fsspec.open(
    "dandi://dandi/000026/rawdata/sub-I38/ses-MRI/anat/"
    "sub-I38_ses-MRI-echo-4_flip-4_VFA.json"
) as f:
    info = json.load(f)
```

### Bound to a dandiset

```python
from dandifs import DandiFileSystem

fs = DandiFileSystem("000026")            # bind to a dandiset (+ version)
fs.ls("rawdata")                          # browse
fs.glob("**/anat/*.json")                 # glob assets
with fs.open("rawdata/sub-I38/.../VFA.json") as f:
    data = f.read()
```

### Files inside a Zarr asset

```python
fs = DandiFileSystem("000108")

fs.ls("path/to/image.zarr")               # list entries inside the Zarr
fs.info("path/to/image.zarr/0/0/0")       # stat a chunk
with fs.open("path/to/image.zarr/0/0/0", "rb") as f:
    chunk = f.read()                      # read chunk bytes
```

This also works transparently with libraries that open a store through fsspec,
e.g. `zarr.open("dandi://dandi/<id>/path/to/image.zarr")`.

### Async

`DandiFileSystem` is a real async filesystem. Pass `asynchronous=True` and
await the coroutine methods (`_ls`, `_info`, `_cat_file`, `_exists`, `_glob`):

```python
fs = DandiFileSystem("000026", asynchronous=True)
entries = await fs._ls("rawdata")
data = await fs._cat_file("rawdata/sub-I38/.../VFA.json")
```

The synchronous API is generated from these coroutines by fsspec and runs on
fsspec's shared background event loop, so it also works inside a running loop
(e.g. Jupyter).

## Authentication

Public dandisets need **no** authentication. For private or embargoed
resources, a token is resolved **lazily** — only after a request returns
`401` — in this order:

1. an explicit `token=` argument to `DandiFileSystem`;
2. the `DANDI_API_KEY` environment variable;
3. a per-instance `<INSTANCE>_API_KEY` variable (e.g. `EMBER_API_KEY`);
4. the system keyring (only if the optional `auth` extra is installed).

```python
fs = DandiFileSystem("000026", token="…")          # explicit
# or: export DANDI_API_KEY=…   (recommended)
```

## Instances

Registered instances: `dandi`, `dandi-staging`, `ember`, `ember-sandbox`, and
`dandi-api-local-docker-tests`. You can also point at any DANDI-schema server:

```python
# by API URL
DandiFileSystem("000001", instance="https://api.my-dandi.org/api")
# by a bare server URL — discovered lazily via the server's /info/ endpoint
DandiFileSystem("000001", instance="https://my-dandi.org")
```

## Licensing

`dandifs` is released under the MIT License (see `LICENSE`). Portions of this
project are derived from [dandi-cli](https://github.com/dandi/dandi-cli)
(Apache-2.0); see the per-file `NOTICE` headers and `OTHER_LICENSES`.
