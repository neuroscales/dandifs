# NOTICE
#   This file was copied and modified from [dandi-cli] dandi/utils.py,
#   which is distributed under the Apache 2.0 license.
#   See: https://github.com/dandi/dandi-cli/blob/master/LICENSE
"""Utilities."""
# stdlib
import datetime
import platform
import sys
from email.utils import parsedate_to_datetime

# external
import dateutil
import requests
from multidict import MultiDict  # dependency of yarl
from yarl import URL

# internal
from . import __version__, get_logger

LOG = get_logger()


USER_AGENT = "dandi/{} requests/{} {}/{}".format(
    __version__,
    requests.__version__,
    platform.python_implementation(),
    platform.python_version(),
)


def is_interactive() -> bool:
    """Return True if all in/outs are tty."""
    # TODO: check on windows if hasattr check would work correctly and
    # add value:
    return sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()


def is_page2_url(page1: str, page2: str) -> bool:
    """
    Tests whether the URL ``page2`` is the same as ``page1`` but with the
    ``page`` query parameter set to ``2``.
    """
    url1 = URL(page1)
    params1 = MultiDict(url1.query)
    params1["page"] = "2"
    url1 = url1.with_query(None)
    url2 = URL(page2)
    params2 = url2.query
    url2 = url2.with_query(None)
    return (url1, sorted(params1.items())) == (url2, sorted(params2.items()))


def joinurl(base: str, path: str) -> str:
    """
    Append a slash-separated ``path`` to a base HTTP(S) URL ``base``.  The two
    components are separated by a single slash, removing any excess slashes
    that would be present after naïve concatenation.

    If ``path`` is already an absolute HTTP(S) URL, it is returned unchanged.

    Note that this function differs from `urllib.parse.urljoin()` when the path
    portion of ``base`` is nonempty and does not end in a slash.
    """
    if path.lower().startswith(("http://", "https://")):
        return path
    else:
        return base.rstrip("/") + "/" + path.lstrip("/")


def get_retry_after(response: requests.Response) -> int | None:
    """If provided and parsed ok, returns duration in seconds to sleep
    before retry.

    If not provided in the response header `Retry-After`, would
    return None.
    If parsing fails, or provided date/sleep does not make sense
    since either too far in the past (over 2 seconds) or in the future
    (over a week), would return None.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return None
    sleep_amount: int | None
    current_date = datetime.datetime.now(datetime.timezone.utc)
    try:
        sleep_amount = int(retry_after)
    except ValueError:
        # else if it is a datestamp like "Wed, 21 Oct 2015 07:28:00 GMT"
        # we could parse it and calculate how long to sleep
        try:
            retry_after_date = parsedate_to_datetime(retry_after)
        except (ValueError, TypeError) as exc_ve:
            # our code or response is wrong, do not crash but issue warning
            # and continue with "if_unparsable" sleep logic
            sleep_amount = None
            LOG.warning(
                "response %d has incorrect date in Retry-After=%s: %s. "
                "Returning %r",
                response.status_code,
                retry_after,
                exc_ve,
                sleep_amount,
            )
        else:
            difference = retry_after_date - current_date
            sleep_amount = int(difference.total_seconds())

    if sleep_amount:
        if -2 < sleep_amount < 0:
            # allow for up to a few seconds delay in us receiving/parsing etc
            # but otherwise assume abnormality and just return if_unparsable
            sleep_amount = 0
        elif sleep_amount < 0:
            sleep_amount = None
            LOG.warning(
                "date in Retry-After=%s is in the past (current is %r). "
                "Returning %r",
                retry_after,
                current_date,
                sleep_amount,
            )
        elif sleep_amount > 7 * 24 * 60 * 60:  # week
            sleep_amount = None
            LOG.warning(
                "date in Retry-After=%s is over a week in the future "
                "(current is %r). "
                "Returning %r",
                retry_after,
                current_date,
                sleep_amount,
            )
    return sleep_amount


def fromisoformat(t: str) -> datetime.datetime:  # noqa: D103
    # datetime.fromisoformat "does not support parsing arbitrary ISO 8601
    # strings" <https://docs.python.org/3/library/datetime.html>.  In
    # particular, it does not parse the time zone suffixes recently
    # introduced into timestamps provided by the API.  Hence, we need to use
    # dateutil instead.
    return dateutil.parser.isoparse(t)


def ensure_datetime(
    t: datetime.datetime | int | float | str,
    strip_tzinfo: bool = False,
    tz: datetime.tzinfo | None = None,
) -> datetime.datetime:
    """Ensures that time is a datetime.

    strip_tzinfo applies only to str records passed in

    epoch time assumed to be local (not utc)
    """
    if isinstance(t, datetime.datetime):
        pass
    elif isinstance(t, (int, float)):
        t = datetime.datetime.fromtimestamp(t).astimezone()
    elif isinstance(t, str):
        # could be in different formats, for now parse as ISO
        t = fromisoformat(t)
        if strip_tzinfo and t.tzinfo:
            # TODO: check a proper way to handle this so we could account
            # for a possibly present tz
            t = t.replace(tzinfo=None)
    else:
        raise TypeError(f"Do not know how to convert {t!r} to datetime")
    if tz:
        t = t.astimezone(tz=tz)
    return t
