import hashlib
import json
import logging
import os

import dateutil.parser

from HTTPArchive import constants

BIGQUERY_MAX_INT = 2**63 - 1


def dict_subset(original_dict, wanted_keys):
    if not original_dict or not wanted_keys:
        return None

    new_dict = dict()
    for k, v in original_dict.items():
        if k.lower() in map(str.lower, wanted_keys):
            new_dict[k] = v
    return new_dict


def get_url_hash(url):
    return int(hashlib.md5(url.encode()).hexdigest()[0:4], 16)


def get_ext(ext):
    ret_ext = ext
    i_q = ret_ext.find("?")
    if i_q > -1:
        ret_ext = ret_ext[:i_q]

    ret_ext = ret_ext[ret_ext.rfind("/") + 1:]
    i_dot = ret_ext.rfind(".")
    if i_dot == -1:
        ret_ext = ""
    else:
        ret_ext = ret_ext[i_dot + 1:]
        if len(ret_ext) > 5:
            # This technique can find VERY long strings that are not file extensions. Try to weed those out.
            ret_ext = ""

    return ret_ext


# When adding to this make sure you also add to transformation.py aggregate_stats
def pretty_type(mime_typ, ext):
    mime_typ = mime_typ.lower()

    # Order by most unique first.
    # Do NOT do html because "text/html" is often misused for other types. We catch it below.
    for typ in ["font", "css", "image", "script", "video", "audio", "xml"]:
        if typ in mime_typ:
            return typ

    # Special cases I found by manually searching.
    if ext  == "js":
        return "script"
    elif "json" in mime_typ or ext == "json":
        return "json"
    elif ext in ["eot", "ttf", "woff", "woff2", "otf"]:
        return "font"
    elif ext in [
        "png",
        "gif",
        "jpg",
        "jpeg",
        "webp",
        "ico",
        "svg",
        "avif",
        "jxl",
        "heic",
        "heif",
    ]:
        return "image"
    elif ext == "css":
        return "css"
    elif ext == "xml":
        return "xml"
    # Video extensions mp4, webm, ts, m4v, m4s, m4v, mov, ogv
    elif next(
        (typ for typ in ["flash", "webm", "mp4", "flv"] if typ in mime_typ), None
    ) or ext in ["mp4", "webm", "ts", "m4v", "m4s", "mov", "ogv", "swf", "f4v", "flv"]:
        return "video"
    elif "wasm" in mime_typ or ext == "wasm":
        return "wasm"
    elif "html" in mime_typ or ext in ["html", "htm"]:
        # Here is where we catch "text/html" mime type.
        return "html"
    elif "text" in mime_typ:
        # Put "text" LAST because it's often misused so $ext should take precedence.
        return "text"
    else:
        return "other"


def get_format(pretty_typ, mime_typ, ext):
    if "image" == pretty_typ:
        # Order by most popular first.
        for typ in [
            "jpg",
            "png",
            "gif",
            "webp",
            "svg",
            "ico",
            "avif",
            "jxl",
            "heic",
            "heif",
        ]:
            if typ in mime_typ or typ == ext:
                return typ
        if "jpeg" in mime_typ:  # pragma: no branch
            return "jpg"
    if "video" == pretty_typ:
        # Order by most popular first.
        for typ in ["flash", "swf", "mp4", "flv", "f4v"]:  # pragma: no branch
            if typ in mime_typ or typ == ext:
                return typ
    return ""


# Headers can appear multiple times, so we have to concat them all then add them to avoid setting a column twice.
def parse_header(input_headers, standard_headers, cookie_key, output_headers=None):
    if output_headers is None:
        output_headers = {}
    other = []
    cookie_size = 0
    for header in input_headers:
        name = header["name"]
        lc_name = name.lower()
        value = header["value"][:255]
        orig_value = header["value"]
        if lc_name in standard_headers.keys():
            # This is one of the standard headers we want to save
            column = standard_headers[lc_name]
            if output_headers.get(column):
                output_headers[column].append(value)
            else:
                output_headers[column] = [value]
        elif cookie_key == lc_name:
            # We don't save the Cookie header, just the size.
            cookie_size += len(orig_value)
        else:
            # All other headers are lumped together.
            other.append("{} = {}".format(name, orig_value))

    # output_headers = {k: ", ".join(v) for k, v in output_headers.items()}
    ret_other = ", ".join(other)

    return output_headers, ret_other, cookie_size


def date_and_client_from_file_name(file_name):
    dir_name, base_name = os.path.split(file_name)
    date = crawl_date(dir_name)
    client = client_name(file_name)
    return date, client


def client_name(file_name):
    try:
        dir_name, base_name = os.path.split(file_name)
        client = dir_name.split("/")[-1].split("-")[0]

        if client == "chrome" or "_Dx" in base_name:
            return "desktop"
        elif client == "android" or "_Mx" in base_name:
            return "mobile"
        else:
            return client.lower()
    except Exception:
        return "desktop"

def format_table_name(row, dataset):
    try:
        return f"{dataset}.{row['date']}_{row['client']}"
    except Exception:
        logging.exception(f"Unable to determine full table name. {dataset=},{row=}")
        raise


def datetime_to_epoch(dt, status_info):
    try:
        return int(round(dateutil.parser.parse(dt).timestamp()))
    except dateutil.parser.ParserError:
        logging.warning(
            f"Could not parse datetime to epoch. dt={dt},status_info={status_info}"
        )
        return None


def crawl_date(dir_name):
    date = None
    try:
        date = dateutil.parser.parse(dir_name.split("/")[-1].split("-")[1].replace("_", " "))
    except Exception:
        from datetime import datetime, timezone
        date = dateutil.parser.parse(datetime.now(tz=timezone.utc).strftime("%y%m%d"))
    return date

def clamp_integer(n):
    try:
        if int(n) < 0:
            return None
        return min(BIGQUERY_MAX_INT, int(n))
    except Exception:
        return None

# given a list of integer columns, clamp data to utils.BIGQUERY_MAX_INT and log violations
def clamp_integers(data, columns):
    violations = {}
    for k, v in data.items():
        if k in columns and v and int(v) > BIGQUERY_MAX_INT:
            violations[k] = v
            data[k] = clamp_integer(v)
    if violations:  # pragma: no branch
        logging.warning(f"Clamping required for {violations}. data={data}")


def int_columns_for_schema(schema_name):
    schema = constants.BIGQUERY["schemas"][schema_name]["fields"]
    return [field["name"] for field in schema if field["type"] == "INTEGER"]


def is_home_page(element):
    if not element:
        # assume False by default
        return False
    metadata = element.get("metadata")
    if metadata:
        # use metadata.crawl_depth starting from 2022-05
        if isinstance(metadata, dict):
            return metadata.get("crawl_depth", 0) == 0
        else:
            return json.loads(metadata).get("crawl_depth", 0) == 0
    else:
        # legacy crawl data is all home-page only (i.e. no secondary pages)
        return True
