import datetime
import json
import logging
import os
import re

from dateutil import parser as date_parser

from HTTPArchive import constants, utils

class HarJsonToSummary:
    @staticmethod
    def initialize_status_info(file_name, page):
        # file name parsing kept for backward compatibility before 2022-03-01
        dir_name, base_name = os.path.split(file_name)

        date, client_name = utils.date_and_client_from_file_name(file_name)

        metadata = page.get("_metadata")
        if metadata is None:
            metadata = {}

        return {
            "archive": "All",  # only one value found when porting logic from PHP
            "label": "{dt:%b} {dt.day} {dt.year}".format(dt=date),
            "crawlid": metadata.get("crawlid", 0),
            "wptid": page.get("testID", base_name.split(".")[0]),
            "medianRun": 1,  # only available in RAW json (median.firstview.run), not HAR json
            "page": metadata.get("tested_url", ""),
            "pageid": utils.clamp_integer(
                # `_metadata.pageid` used on and prior to 2022-03-01, `_metadata.page_id` used going forward
                metadata["page_id"] if "page_id" in metadata else metadata.get("pageid", 0)
            ),
            "rank": utils.clamp_integer(metadata["rank"]) if metadata.get("rank") else None,
            "date": "{:%Y_%m_%d}".format(date),
            "client": metadata.get("layout", client_name).lower(),
            "metadata": metadata,
        }

    @staticmethod
    def generate_pages(file_name, element):
        if not element:
            logging.warning("HAR file read error.")
            return None, None

        if isinstance(element, str):
            try:
                har = json.loads(element)
            except json.JSONDecodeError:
                logging.warning(f"JSON decode failed for: {file_name}")
                return None, None
        elif isinstance(element, dict):
            har = element
        else:
            logging.exception(f"Unexpected type for: {file_name}")
            return None, None

        log = har["log"]
        pages = log["pages"]
        if len(pages) == 0:
            logging.warning(f"No pages found for: {file_name}")
            return None, None

        status_info = HarJsonToSummary.initialize_status_info(file_name, pages[0])

        try:
            page = HarJsonToSummary.import_page(pages[0], status_info)
        except Exception:
            logging.warning(
                f"import_page() failed for status_info:{status_info}", exc_info=True
            )
            return None, None

        entries, first_url, first_html_url = HarJsonToSummary.import_entries(
            log["entries"], status_info
        )
        if not entries or entries == [None]:
            logging.warning(f"import_entries() failed for status_info:{status_info}")
            return None, None
        else:
            agg_stats = HarJsonToSummary.aggregate_stats(
                entries, first_url, first_html_url, status_info
            )
            if not agg_stats:
                logging.warning(
                    f"aggregate_stats() failed for status_info:{status_info}"
                )
                return None, None
            else:
                page.update(agg_stats)

        utils.clamp_integers(page, utils.int_columns_for_schema("pages"))
        for entry in entries:
            if not entry:
                logging.warning("Empty entry")
                continue
            utils.clamp_integers(entry, utils.int_columns_for_schema("requests"))

        return page, entries

    @staticmethod
    def import_entries(entries, status_info):
        requests = []
        first_url = ""
        first_html_url = ""
        entry_number = 0

        for entry in entries:
            if not entry:
                logging.warning(f"Empty entry, status_info:{status_info}")
                continue
            try:
                (
                    request,
                    first_url,
                    first_html_url,
                    entry_number,
                ) = HarJsonToSummary.summarize_entry(
                    entry, first_url, first_html_url, entry_number, status_info
                )
                requests.append(request)
            except RuntimeError as e:
                logging.warning(e, exc_info=True)

        return requests, first_url, first_html_url

    @staticmethod
    def summarize_entry(entry, first_url, first_html_url, entry_number, status_info):
        if entry.get("_number"):
            entry_number = entry["_number"]
        else:
            entry_number += 1

        ret_request = {
            "client": status_info["client"],
            "date": status_info["date"],
            "metadata": status_info["metadata"],
            # we use this below for expAge calculation
            "time": entry["time"],
            "_cdn_provider": entry.get("_cdn_provider"),
            # amount response WOULD have been reduced if it had been gzipped
            "_gzip_save": entry.get("_gzip_save"),
        }
        # REQUEST
        try:
            request = entry["request"]
        except KeyError as e:
            raise RuntimeError(
                f"Entry does not contain a request, status_info={status_info}, entry={entry}"
            ) from e

        url = request["url"]
        (
            request_headers,
            request_other_headers,
            request_cookie_size,
        ) = utils.parse_header(
            request["headers"], constants.GH_REQ_HEADERS, cookie_key="cookie"
        )

        req_headers_size = (
            request.get("headersSize")
            if int(request.get("headersSize", 0)) > 0
            else None
        )
        req_body_size = (
            request.get("bodySize") if int(request.get("bodySize", 0)) > 0 else None
        )

        ret_request.update(
            {
                "method": request["method"],
                "httpVersion": request["httpVersion"],
                "url": url,
                "urlShort": url[:255],
                "reqHeadersSize": req_headers_size,
                "reqBodySize": req_body_size,
                "reqCookieLen": request_cookie_size,
            }
        )

        # RESPONSE
        response = entry["response"]
        status = response["status"]

        resp_headers_size = (
            response.get("headersSize")
            if int(response.get("headersSize", 0)) > 0
            else None
        )
        resp_body_size = (
            response.get("bodySize")
            if int(response.get("bodySize", 0)) > 0
            else None
        )

        ret_request.update(
            {
                "status": status,
                "respHttpVersion": response["httpVersion"],
                "redirectUrl": response.get("url"),
                "respHeadersSize": resp_headers_size,
                "respBodySize": resp_body_size,
                "respSize": response["content"]["size"],
            }
        )

        # TODO revisit this logic - is this the right way to get extention, type, format from mimetype?
        #  consider using mimetypes library instead https://docs.python.org/3/library/mimetypes.html
        mime_type = response["content"]["mimeType"]
        ext = utils.get_ext(url)
        typ = utils.pretty_type(mime_type, ext)
        frmt = utils.get_format(typ, mime_type, ext)

        ret_request.update(
            {
                "mimeType": mime_type.lower(),
                "ext": ext.lower(),
                "format": frmt.lower(),
            }
        )

        (
            response_headers,
            response_other_headers,
            response_cookie_size,
        ) = utils.parse_header(
            response["headers"],
            constants.GH_RESP_HEADERS,
            cookie_key="set-cookie",
            output_headers=request_headers,
        )
        ret_request.update(
            {
                "respCookieLen": response_cookie_size,
            }
        )

        # calculate expAge - number of seconds before resource expires
        exp_age = 0
        cc = (
            request_headers.get("resp_cache_control")[0]
            if "resp_cache_control" in request_headers
            else None
        )
        if cc and ("must-revalidate" in cc or "no-cache" in cc or "no-store" in cc):
            # These directives dictate the response can NOT be cached.
            exp_age = 0
        elif cc and re.match(r"max-age=\d+", cc):
            try:
                exp_age = utils.clamp_integer(re.findall(r"\d+", cc)[0])
            except Exception:
                # TODO compare results from old and new pipeline for these errors
                logging.warning(f"Unable to parse max-age, cc:{cc}", exc_info=True)
        elif "resp_expires" in response_headers:
            # According to RFC 2616 ( http://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html#sec13.2.4 ):
            #     freshness_lifetime = expires_value - date_value
            # If the Date: response header is present, we use that.
            # Otherwise, fall back to $startedDateTime which is based on the client so might suffer from clock skew.
            try:
                start_date = (
                    date_parser.parse(response_headers.get("resp_date")[0]).timestamp()
                    if "resp_date" in response_headers
                    else ret_request["startedDateTime"]
                )
                end_date = date_parser.parse(
                    response_headers["resp_expires"][0]
                ).timestamp()
                # TODO try regex to resolve issues parsing apache ExpiresByType Directive
                #   https://httpd.apache.org/docs/2.4/mod/mod_expires.html#expiresbytype
                # end_date = date_parser.parse(
                #   re.findall(r"\d+", response_headers['resp_expires'][0])[0]).timestamp()
                exp_age = end_date - start_date
            except Exception:
                pass
                """
                logging.warning(
                    f"Could not parse dates. "
                    f"start=(resp_date:{response_headers.get('resp_date')},"
                    f"startedDateTime:{ret_request.get('startedDateTime')}), "
                    f"end=(resp_expires:{response_headers.get('resp_expires')}), "
                    f"status_info:{status_info}",
                    exc_info=True,
                )
                """

        ret_request.update({"expAge": int(max(exp_age, 0))})

        # TODO implement custom rules?
        # https://github.com/HTTPArchive/legacy.httparchive.org/blob/de08e0c7c94a7da529826f0a4429a9d28b8fdf5e/bulktest/batch_lib.inc#L658-L664

        # TODO consider doing this sooner? why process if status is bad?
        # wrap it up
        first_req = False
        first_html = False
        if not first_url:
            if (400 <= status <= 599) or 12000 <= status:
                logging.warning(
                    f"The first request ({url}) failed with status {status}. status_info={status_info}"
                )
                return None, None, None, None
            # This is the first URL found associated with the page - assume it's the base URL.
            first_req = True
            first_url = url

        if not first_html_url:
            # This is the first URL found associated with the page that's HTML.
            first_html = True
            first_html_url = url

        return ret_request, first_url, first_html_url, entry_number

    @staticmethod
    def import_page(page, status_info):
        on_load = (
            page.get("_docTime")
            if page.get("_docTime") != 0
            else max(page.get("_visualComplete"), page.get("_fullyLoaded"))
        )
        document_height = (
            page["_document_height"]
            if page.get("_document_height") and int(page["_document_height"]) > 0
            else 0
        )
        document_width = (
            page["_document_width"]
            if page.get("_document_width") and int(page["_document_width"]) > 0
            else 0
        )
        localstorage_size = (
            page["_localstorage_size"]
            if page.get("_localstorage_size") and int(page["_localstorage_size"]) > 0
            else 0
        )
        sessionstorage_size = (
            page["_sessionstorage_size"]
            if page.get("_sessionstorage_size")
            and int(page["_sessionstorage_size"]) > 0
            else 0
        )

        avg_dom_depth = (
            int(float(page.get("_avg_dom_depth"))) if page.get("_avg_dom_depth") else 0
        )

        doc_type = (
            str(page.get("_doctype"))
            if page.get("_doctype")
            else None
        )

        return {
            "metadata": json.dumps(status_info["metadata"]),
            "client": status_info["client"],
            "date": status_info["date"],
            "pageid": status_info["pageid"],
            "createDate": utils.clamp_integer(datetime.datetime.now().timestamp()),
            "startedDateTime": utils.datetime_to_epoch(
                page["startedDateTime"], status_info
            ),
            "archive": status_info["archive"],
            "label": status_info["label"],
            "crawlid": status_info["crawlid"],
            "url": status_info["page"],
            "urlhash": utils.get_url_hash(status_info["page"]),
            "urlShort": status_info["page"][:255],
            "TTFB": page.get("_TTFB"),
            "renderStart": page.get("_render"),
            "fullyLoaded": page.get("_fullyLoaded"),
            "visualComplete": page.get("_visualComplete"),
            "onLoad": on_load,
            "gzipTotal": page.get("_gzip_total"),
            "gzipSavings": page.get("_gzip_savings"),
            "numDomElements": page.get("_domElements"),
            "onContentLoaded": page.get("_domContentLoadedEventStart"),
            "cdn": page.get("_base_page_cdn"),
            "SpeedIndex": page.get("_SpeedIndex"),
            "PageSpeed": page.get("_pageSpeed", {}).get("score"),
            "_connections": page.get("_connections"),
            "_adult_site": page.get("_adult_site", False),
            "avg_dom_depth": avg_dom_depth,
            "doctype": doc_type,
            "document_height": document_height,
            "document_width": document_width,
            "localstorage_size": localstorage_size,
            "sessionstorage_size": sessionstorage_size,
            "meta_viewport": page.get("_meta_viewport"),
            "num_iframes": page.get("_num_iframes"),
            "num_scripts": page.get("_num_scripts"),
            "num_scripts_sync": page.get("_num_scripts_sync"),
            "num_scripts_async": page.get("_num_scripts_async"),
            "usertiming": page.get("_usertiming"),
        }

    @staticmethod
    def aggregate_stats(entries, first_url, first_html_url, status_info):
        if not entries or entries == [None]:
            logging.error(f"No entries found. status_info={status_info}")
            return None
        if not first_url:
            logging.error(f"No first URL found. status_info={status_info}")
            return None
        if not first_html_url:
            logging.error(f"No first HTML URL found. status_info={status_info}")
            return None

        # initialize variables for counting the page's stats
        bytes_total = 0
        req_total = 0
        size = {}
        count = {}

        # This is a list of all mime types AND file formats that we care about.
        typs = [
            "css",
            "image",
            "script",
            "html",
            "font",
            "other",
            "audio",
            "video",
            "text",
            "json",
            "xml",
            "gif",
            "jpg",
            "png",
            "webp",
            "svg",
            "avif",
            "jxl",
            "heic",
            "heif",
            "ico",
            "flash",
            "swf",
            "mp4",
            "flv",
            "f4v",
            "wasm",
        ]
        # initialize the hashes
        for typ in typs:
            size[typ] = 0
            count[typ] = 0
        domains = {}
        maxage_null = (
            max_age_0
        ) = max_age_1 = max_age_30 = max_age_365 = max_age_more = 0
        bytes_html_doc = (
            num_redirects
        ) = num_errors = num_glibs = num_https = num_compressed = max_domain_reqs = 0

        for entry in entries:
            if not entry:
                logging.warning(f"Empty entry, status_info:{status_info}")
                continue
            url = entry["urlShort"]
            pretty_type = entry["type"]
            resp_size = int(entry["respSize"])
            req_total += 1
            bytes_total += resp_size
            count[pretty_type] += 1
            size[pretty_type] += resp_size

            frmt = entry.get("format")
            if frmt and pretty_type in ["image", "video"]:
                if frmt not in typs:
                    logging.warning(
                        f"Unexpected type, found format:{frmt}, status_info:{status_info}"
                    )
                else:
                    count[frmt] += 1
                    size[frmt] += resp_size

            # count unique domains (really hostnames)
            matches = re.findall(r"(?:http|ws)[s]*://([^/]*)", url)
            if url and matches:
                hostname = matches[0]
                if hostname not in domains:
                    domains[hostname] = 0
                else:
                    domains[hostname] += 1
            else:
                logging.warning(
                    f"No hostname found in URL: {url}. status_info={status_info}"
                )

            # count expiration windows
            exp_age = entry.get("expAge")
            day_secs = 24 * 60 * 60
            if not exp_age:
                maxage_null += 1
            elif int(exp_age) == 0:
                max_age_0 += 1
            elif exp_age <= day_secs:
                max_age_1 += 1
            elif exp_age <= 30 * day_secs:
                max_age_30 += 1
            elif exp_age <= 365 * day_secs:
                max_age_365 += 1
            else:
                max_age_more += 1

            if entry.get("firstHtml"):
                bytes_html_doc = resp_size

            status = entry.get("status")
            if 300 <= status < 400 and status != 304:
                num_redirects += 1
            elif 400 <= status < 600:
                num_errors += 1

            if url.startswith("https://"):
                num_https += 1

            if "googleapis.com" in entry.get("req_host", ""):
                num_glibs += 1

            if (
                entry.get("resp_content_encoding", "") == "gzip"
                or entry.get("resp_content_encoding", "") == "deflate"
            ):
                num_compressed += 1

        for domain in domains:
            max_domain_reqs = max(max_domain_reqs, domains[domain])

        ret = {
            "reqTotal": req_total,
            "bytesTotal": utils.clamp_integer(bytes_total),
            "reqJS": count["script"],
            "bytesJS": utils.clamp_integer(size["script"]),
            "reqImg": count["image"],
            "bytesImg": utils.clamp_integer(size["image"]),
            "reqJson": 0,
            "bytesJson": 0,
        }
        for typ in typs:
            ret.update(
                {
                    "req{}".format(typ.title()): count[typ],
                    "bytes{}".format(typ.title()): utils.clamp_integer(size[typ]),
                }
            )

        ret.update(
            {
                "numDomains": len(domains),
                "maxageNull": maxage_null,
                "maxage0": max_age_0,
                "maxage1": max_age_1,
                "maxage30": max_age_30,
                "maxage365": max_age_365,
                "maxageMore": max_age_more,
                "bytesHtmlDoc": utils.clamp_integer(bytes_html_doc),
                "numRedirects": num_redirects,
                "numErrors": num_errors,
                "numGlibs": num_glibs,
                "numHttps": num_https,
                "numCompressed": num_compressed,
                "maxDomainReqs": max_domain_reqs,
                "wptid": status_info["wptid"],
                "wptrun": status_info["medianRun"],
                "rank": status_info["rank"],
            }
        )

        return ret
