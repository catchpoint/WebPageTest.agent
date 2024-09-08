"""HTTP Archive dataflow pipeline for generating HAR data on BigQuery."""

from __future__ import absolute_import

import json
import logging
import re
from copy import deepcopy
from hashlib import sha256
from typing import Union, Dict

from HTTPArchive import constants, utils
from HTTPArchive.transformation import HarJsonToSummary

def get_page(file_name, har):
    """Parses the page from a HAR object."""

    if not har:
        return None

    date, client = utils.date_and_client_from_file_name(file_name)

    page = har.get("log").get("pages")[0]
    url = page.get("_URL")
    wptid = page.get("testID")
    date = "{:%Y-%m-%d}".format(date)
    is_root_page = True
    root_page = url
    rank = None

    metadata = page.get("_metadata")
    if metadata:
        # The page URL from metadata is more accurate.
        # See https://github.com/HTTPArchive/data-pipeline/issues/48
        url = metadata.get("tested_url", url)
        client = metadata.get("layout", client).lower()
        is_root_page = metadata.get("crawl_depth", 0) == 0
        root_page = metadata.get("root_page_url", url)
        rank = int(metadata.get("rank")) if metadata.get("rank") else None

    try:
        payload = trim_page(page)
        payload_json = to_json(payload)
    except ValueError:
        logging.warning(
            'Skipping pages payload for "%s": unable to stringify as JSON.', wptid
        )
        return None

    custom_metrics = get_custom_metrics(page, wptid)
    lighthouse = get_lighthouse_reports(har, wptid)
    features = get_features(page, wptid)
    technologies = get_technologies(page)

    summary_page = None
    try:
        summary_page, _ = HarJsonToSummary.generate_pages(file_name, har)
        wanted_summary_fields = [
            field["name"]
            for field in constants.BIGQUERY["schemas"]["summary_pages"]["fields"]
        ]
        summary_page = utils.dict_subset(summary_page, wanted_summary_fields)
        summary_page = json.dumps(summary_page)
    except Exception:
        logging.exception(
            f"Unable to unpack HAR, check previous logs for detailed errors. "
            f"{file_name=}, {har=}"
        )

    ret = {
        "date": date,
        "client": client,
        "page": url,
        "is_root_page": is_root_page,
        "root_page": root_page,
        "rank": rank,
        "wptid": wptid,
        "payload": payload_json,
        "summary": summary_page,
        "custom_metrics": custom_metrics,
        "lighthouse": lighthouse,
        "features": features,
        "technologies": technologies,
        "metadata": to_json(metadata) if metadata else None,
    }

    if json_exceeds_max_content_size(ret, "pages", wptid):
        # try dropping the payload since it is already processed
        ret['payload'] = '{}'
        if json_exceeds_max_content_size(ret, "pages", wptid):
            # try stripping out any REALLY large custom metrics or lighthouse results
            if len('custom_metrics') > 1000000:
                ret['custom_metrics'] = get_custom_metrics(page, wptid, 100000)
            if len('lighthouse') > 1000000:
                ret['lighthouse'] = '{}'
            if json_exceeds_max_content_size(ret, "pages", wptid):
                ret['lighthouse'] = '{}'
                if json_exceeds_max_content_size(ret, "pages", wptid):
                    ret['custom_metrics'] = '{}'
                    if json_exceeds_max_content_size(ret, "pages", wptid):
                        # Give up. Can't get this result to be small enough
                        return None

    return [ret]


def get_custom_metrics(page, wptid, max_size=None):
    """Transforms the page data into a custom metrics object."""

    page_metrics = page.get("_custom")

    if not page_metrics:
        logging.warning(f"No `_custom` attribute in page for {wptid=}")
        return None

    custom_metrics = {}

    for metric in page_metrics:
        if metric == 'parsed_css':
            continue

        value = page.get(f"_{metric}")

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                logging.warning(
                    "ValueError: Unable to parse custom metric %s as JSON for %s",
                    metric,
                    wptid,
                )
                continue
            except RecursionError:
                logging.warning(
                    "RecursionError: Unable to parse custom metric %s as JSON for %s",
                    metric,
                    wptid,
                )
                continue

        if max_size is not None:
            value_json = to_json(value)
            if value_json and len(value_json) <= max_size:
                custom_metrics[metric] = value
        else:
            custom_metrics[metric] = value

    try:
        custom_metrics_json = to_json(custom_metrics)
    except UnicodeEncodeError:
        logging.warning("Unable to JSON encode custom metrics for %s", wptid)
        return None

    return custom_metrics_json


def get_features(page, wptid):
    """Parses the features from a page."""

    if not page:
        return None

    blink_features = page.get("_blinkFeatureFirstUsed")

    if not blink_features:
        return None

    def get_feature_names(feature_map, feature_type):
        feature_names = []

        try:
            for (key, value) in feature_map.items():
                if value.get("name"):
                    feature_names.append(
                        {"feature": value.get("name"), "type": feature_type, "id": key}
                    )
                    continue

                match = id_pattern.match(key)
                if match:
                    feature_names.append(
                        {"feature": "", "type": feature_type, "id": match.group(1)}
                    )
                    continue

                feature_names.append({"feature": key, "type": feature_type, "id": ""})
        except (ValueError, AttributeError):
            logging.warning(
                "Unable to get feature names for %s. Feature_type: %s, feature_map: %s",
                wptid,
                feature_type,
                feature_map,
                exc_info=True,
            )

        return feature_names

    id_pattern = re.compile(r"^Feature_(\d+)$")

    return (
        get_feature_names(blink_features.get("Features"), "default")
        + get_feature_names(blink_features.get("CSSFeatures"), "css")
        + get_feature_names(blink_features.get("AnimatedCSSFeatures"), "animated-css")
    )


def get_technologies(page):
    """Parses the technologies from a page."""

    if not page:
        return None

    app_names = page.get("_detected_apps", {})
    categories = page.get("_detected", {})

    # When there are no detected apps, it appears as an empty array.
    if isinstance(app_names, list):
        app_names = {}
        categories = {}

    technologies = {}
    app_map = {}
    for app, info_list in app_names.items():
        if not info_list:
            continue

        # There may be multiple info values. Add each to the map.
        for info in info_list.split(","):
            app_id = f"{app} {info}" if len(info) > 0 else app
            app_map[app_id] = app

    for category, apps in categories.items():
        for app_id in apps.split(","):
            app = app_map.get(app_id)
            info = ""
            if app is None:
                app = app_id
            else:
                info = app_id[len(app):].strip()

            technologies[app] = technologies.get(
                app, {"technology": app, "info": [], "categories": []}
            )

            technologies.get(app).get("info").append(info)
            if category not in technologies.get(app).get("categories"):
                technologies.get(app).get("categories").append(category)

    return list(technologies.values())


def get_lighthouse_reports(har, wptid):
    """Parses Lighthouse results from a HAR object."""

    if not har:
        return None

    report = har.get("_lighthouse")

    if not report:
        return None

    # Omit large UGC.
    report.get("audits").get("screenshot-thumbnails", {}).get("details", {}).pop(
        "items", None
    )

    try:
        report_json = to_json(report)
    except ValueError:
        logging.warning(
            "Skipping Lighthouse report for %s: unable to stringify as JSON.", wptid
        )
        return None

    if json_exceeds_max_content_size(report_json, "Lighthouse report", wptid):
        return None

    return report_json


def get_requests(file_name, har):
    """Parses the requests from the HAR."""

    if not har:
        return None

    date, client = utils.date_and_client_from_file_name(file_name)

    page = har.get("log").get("pages")[0]
    page_url = page.get("_URL")
    date = "{:%Y-%m-%d}".format(date)
    is_root_page = True
    root_page = page_url
    rank = None
    wptid = page.get("testID")

    metadata = page.get("_metadata")
    if metadata:
        # The page URL from metadata is more accurate.
        # See https://github.com/HTTPArchive/data-pipeline/issues/48
        page_url = metadata.get("tested_url", page_url)
        client = metadata.get("layout", client).lower()
        is_root_page = metadata.get("crawl_depth", 0) == 0
        root_page = metadata.get("root_page_url", page_url)
        rank = int(metadata.get("rank")) if metadata.get("rank") else None

    entries = har.get("log").get("entries")

    requests = []
    index = 0

    # State information for the request summary processing
    first_url = None
    first_html_url = None
    entry_number = 0

    for request in entries:

        request_url = request.get("_full_url")
        is_main_document = request.get("_final_base_page", False)
        index = int(request.get("_index", index))
        request_headers = []
        response_headers = []

        index += 1
        if not request_url:
            logging.warning(
                'Skipping empty request URL for "%s" index %s', page_url, index
            )
            continue

        if request.get("request") and request.get("request").get("headers"):
            request_headers = request.get("request").get("headers")

        if request.get("response") and request.get("response").get("headers"):
            response_headers = request.get("response").get("headers")

        try:
            payload = to_json(trim_request(request))
        except ValueError:
            logging.warning(
                'Skipping requests payload for "%s": ' "unable to stringify as JSON.",
                request_url,
            )
            continue

        response_body = None
        if request.get("response") and request.get("response").get("content"):
            response_body = request.get("response").get("content").get("text", None)

            if response_body is not None:
                response_body = response_body[:constants.MaxContentSize.RESPONSE_BODIES.value]

        mime_type = request.get("response").get("content").get("mimeType")
        ext = utils.get_ext(request_url)
        type = utils.pretty_type(mime_type, ext)

        summary_request = None
        try:
            status_info = HarJsonToSummary.initialize_status_info(file_name, page)
            summary_request, first_url, first_html_url, entry_number = HarJsonToSummary.summarize_entry(
                request, first_url, first_html_url, entry_number, status_info
            )
            wanted_summary_fields = [
                field["name"]
                for field in constants.BIGQUERY["schemas"]["summary_requests"]["fields"]
            ]
            summary_request = utils.dict_subset(summary_request, wanted_summary_fields)
            summary_request = json.dumps(summary_request)
        except Exception:
            logging.exception(
                f"Unable to unpack HAR, check previous logs for detailed errors. "
                f"{file_name=}, {har=}"
            )

        ret = {
            "date": date,
            "client": client,
            "page": page_url,
            "is_root_page": is_root_page,
            "root_page": root_page,
            "rank": rank,
            "url": request_url,
            "is_main_document": is_main_document,
            "type": type,
            "index": index,
            "payload": payload,
            "summary": summary_request,
            "request_headers": request_headers,
            "response_headers": response_headers,
            "response_body": response_body,
        }

        if json_exceeds_max_content_size(ret, "requests", wptid):
            continue

        requests.append(ret)

    return requests


def hash_url(url):
    """Hashes a given URL to a process-stable integer value."""

    return int(sha256(url.encode("utf-8")).hexdigest(), 16)


def trim_request(request):
    """Removes redundant fields from the request object."""

    if not request:
        return None

    # Make a copy first so the response body can be used later.
    request = deepcopy(request)
    request.get("response", {}).get("content", {}).pop("text", None)
    return request


def trim_page(src_page):
    """Removes unneeded fields from the page object."""

    if not src_page:
        return None

    # Make a copy first so the data can be used later.
    page = deepcopy(src_page)

    # Remove the fields that are parsed out into separate columns
    page.pop("_parsed_css", None)
    page.pop("_lighthouse", None)
    page.pop("_blinkFeatureFirstUsed", None)
    page.pop("_detected_apps", None)
    page.pop("_detected", None)

    # Delete the custom metrics (enumerated in the _custom entry)
    """
    if '_custom' in page:
        for metric in page['_custom']:
            page.pop('_' + metric, None)
        page.pop("_custom", None)
    """

    return page


def to_json(obj):
    """Returns a JSON representation of the object.

    This method attempts to mirror the output of the
    legacy Java Dataflow pipeline. For the most part,
    the default `json.dumps` config does the trick,
    but there are a few settings to make it more consistent:

    - Omit whitespace between properties
    - Do not escape non-ASCII characters (preserve UTF-8)

    One difference between this Python implementation and the
    Java implementation is the way long numbers are handled.
    A Python-serialized JSON string might look like this:

        "timestamp":1551686646079.9998

    while the Java-serialized string uses scientific notation:

        "timestamp":1.5516866460799998E12

    Out of a sample of 200 actual request objects, this was
    the only difference between implementations. This can be
    considered an improvement.
    """

    if not obj:
        raise ValueError

    return (
        json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8", "surrogatepass")
        .decode("utf-8", "replace")
    )


def from_json(file_name, string):
    """Returns an object from the JSON representation."""

    try:
        return [(file_name, json.loads(string))]
    except json.JSONDecodeError as err:
        logging.error('Unable to parse file %s into JSON object "%s...": %s' % (file_name, string[:50], err))
        return None


def json_exceeds_max_content_size(data: Union[Dict, str], typ, wptid):
    max_content_size = constants.MaxContentSize.STREAMING_INSERTS.value
    if isinstance(data, dict):
        payload = to_json(data)
    else:
        payload = data

    size = len(payload.encode("utf-8"))
    oversized = size > max_content_size
    if oversized:
        logging.warning(
            f'Skipping {typ} payload for "{wptid}": '
            f"payload size ({size}) exceeds the maximum content size of {max_content_size} bytes."
        )
    return oversized

def get_parsed_css(file_name, har):
    """Extracts the parsed CSS custom metric from the HAR."""

    if not har:
        return None

    date, client = utils.date_and_client_from_file_name(file_name)
    date = "{:%Y-%m-%d}".format(date)
    page = har.get("log").get("pages")[0]
    page_url = page.get("_URL")

    if not page_url:
        logging.warning("Skipping parsed CSS, no page URL")
        return None

    metadata = page.get("_metadata")
    if metadata:
        page_url = metadata.get("tested_url", page_url)

    is_root_page = True
    if metadata:
        is_root_page = metadata.get("crawl_depth") == 0

    custom_metric = page.get("_parsed_css")

    if not custom_metric:
        logging.warning("No parsed CSS data for page %s", page_url)
        return None

    parsed_css = []

    for entry in custom_metric:
        url = entry.get("url")
        ast = entry.get("ast")

        if url == 'inline':
            # Skip inline styles for now. They're special.
            continue

        try:
            ast_json = to_json(ast)
        except Exception:
            logging.warning(
                'Unable to stringify parsed CSS to JSON for "%s".'
                % page_url
            )
            continue

        parsed_css.append({
            "date": date,
            "client": client,
            "page": page_url,
            "is_root_page": is_root_page,
            "url": url,
            "css": ast_json
        })

    return parsed_css
