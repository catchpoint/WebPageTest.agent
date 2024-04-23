import importlib.resources as pkg_resources
import json
from enum import Enum


def _get_schema(path):
    return json.loads(pkg_resources.read_text("schema", path))


# TODO remove 'experimental' before going live
BIGQUERY = {
    "datasets": {
        "summary_pages_all": "httparchive:experimental_summary_pages",
        "summary_requests_all": "httparchive:experimental_summary_requests",
        "pages_all": "httparchive:experimental_pages",
        "technologies_all": "httparchive:experimental_technologies",
        "lighthouse_all": "httparchive:experimental_lighthouse",
        "requests_all": "httparchive:experimental_requests",
        "response_bodies_all": "httparchive:experimental_response_bodies",
        "parsed_css_all": "httparchive:experimental_parsed_css",
        "summary_pages_home": "httparchive:summary_pages",
        "summary_requests_home": "httparchive:summary_requests",
        "pages_home": "httparchive:pages",
        "technologies_home": "httparchive:technologies",
        "lighthouse_home": "httparchive:lighthouse",
        "requests_home": "httparchive:requests",
        "response_bodies_home": "httparchive:response_bodies",
        "all_pages": "httparchive:all.pages",
        "all_requests": "httparchive:all.requests",
        "parsed_css_home": "httparchive:experimental_parsed_css",
    },
    "schemas": {
        "summary_pages": {"fields": _get_schema("summary_pages.json")},
        "summary_requests": {"fields": _get_schema("summary_requests.json")},
        "pages": {"fields": _get_schema("pages.json")},
        "technologies": {"fields": _get_schema("technologies.json")},
        "lighthouse": {"fields": _get_schema("lighthouse.json")},
        "requests": {"fields": _get_schema("requests.json")},
        "response_bodies": {"fields": _get_schema("response_bodies.json")},
        "parsed_css": {"fields": _get_schema("parsed_css.json")},
        "all_pages": {"fields": _get_schema("all_pages.json")},
        "all_requests": {"fields": _get_schema("all_requests.json")},
    },
    # See BigQuery API JobConfigurationLoad doc for additional parameters
    #   https://cloud.google.com/bigquery/docs/reference/rest/v2/Job#jobconfigurationload
    "additional_bq_parameters": {
        "all_pages": {
            'timePartitioning': {'type': 'DAY', 'field': 'date', 'requirePartitionFilter': True},
            'clustering': {'fields': ['client', 'is_root_page', 'rank']},
            'maxBadRecords': 100,
        },
        "all_requests": {
            'timePartitioning': {'type': 'DAY', 'field': 'date', 'requirePartitionFilter': True},
            'clustering': {'fields': ['client', 'is_root_page', 'is_main_document', 'type']},
            'maxBadRecords': 100,
        },
    },
}

# mapping of headers to DB fields
GH_REQ_HEADERS = {
    "accept": "req_accept",
    "accept-charset": "req_accept_charset",
    "accept-encoding": "req_accept_encoding",
    "accept-language": "req_accept_language",
    "connection": "req_connection",
    "host": "req_host",
    "if-modified-since": "req_if_modified_since",
    "if-none-match": "req_if_none_match",
    "referer": "req_referer",
    "user-agent": "req_user_agent",
}
GH_RESP_HEADERS = {
    "accept-ranges": "resp_accept_ranges",
    "age": "resp_age",
    "cache-control": "resp_cache_control",
    "connection": "resp_connection",
    "content-encoding": "resp_content_encoding",
    "content-language": "resp_content_language",
    "content-length": "resp_content_length",
    "content-location": "resp_content_location",
    "content-type": "resp_content_type",
    "date": "resp_date",
    "etag": "resp_etag",
    "expires": "resp_expires",
    "keep-alive": "resp_keep_alive",
    "last-modified": "resp_last_modified",
    "location": "resp_location",
    "pragma": "resp_pragma",
    "server": "resp_server",
    "transfer-encoding": "resp_transfer_encoding",
    "vary": "resp_vary",
    "via": "resp_via",
    "x-powered-by": "resp_x_powered_by",
}


class MaxContentSize(Enum):
    # BigQuery can handle rows up to 100 MB when using `WriteToBigQuery.Method.FILE_LOADS`
    FILE_LOADS = 100 * 1000000
    # BigQuery can handle rows up to 10 MB when using `WriteToBigQuery.Method.STREAMING_INSERTS`
    STREAMING_INSERTS = 10 * 1000000

    # limit response bodies to 20MB
    RESPONSE_BODIES = 20 * 1000000


TECHNOLOGY_QUERY_ID_KEYS = {
    "adoption":        ["date", "technology", "geo", "rank"],
    "lighthouse":      ["date", "technology", "geo", "rank"],
    "core_web_vitals": ["date", "technology", "geo", "rank"],
    "page_weight":     ["date", "technology", "geo", "rank"],
    "technologies":    ["client", "technology", "category"],
    "categories":      ["category"],
}
"""Mapping of query types to a list of fields that uniquely identify a row."""

# editorconfig-checker-disable
TECHNOLOGY_QUERIES = {
    "adoption": """
        CREATE TEMPORARY FUNCTION GET_ADOPTION(
            records ARRAY<STRUCT<
                client STRING,
                origins INT64
            >>
        ) RETURNS STRUCT<
            desktop INT64,
            mobile INT64
        > LANGUAGE js AS '''
        return Object.fromEntries(records.map(({{client, origins}}) => {{
            return [client, origins];
        }}));
        ''';

        SELECT
            STRING(DATE(date)) as date,
            app AS technology,
            rank,
            geo,
            GET_ADOPTION(ARRAY_AGG(STRUCT(
                client,
                origins
            ))) AS adoption
        FROM
            `httparchive.core_web_vitals.technologies`
        WHERE date = '{date}'
        GROUP BY date, app, rank, geo
        """,
    "lighthouse": """
        CREATE TEMPORARY FUNCTION GET_LIGHTHOUSE(
            records ARRAY<STRUCT<
                client STRING,
                median_lighthouse_score_accessibility NUMERIC,
                median_lighthouse_score_best_practices NUMERIC,
                median_lighthouse_score_performance NUMERIC,
                median_lighthouse_score_pwa NUMERIC,
                median_lighthouse_score_seo NUMERIC
        >>
        ) RETURNS ARRAY<STRUCT<
        name STRING,
        desktop STRUCT<
            median_score NUMERIC
        >,
        mobile STRUCT<
            median_score NUMERIC
        >
        >> LANGUAGE js AS '''
        const METRIC_MAP = {{
            accessibility: 'median_lighthouse_score_accessibility',
            best_practices: 'median_lighthouse_score_best_practices',
            performance: 'median_lighthouse_score_performance',
            pwa: 'median_lighthouse_score_pwa',
            seo: 'median_lighthouse_score_seo',
        }};

        // Initialize the Lighthouse map.
        const lighthouse = Object.fromEntries(Object.keys(METRIC_MAP).map(metricName => {{
            return [metricName, {{name: metricName}}];
        }}));

        // Populate each client record.
        records.forEach(record => {{
            Object.entries(METRIC_MAP).forEach(([metricName, median_score]) => {{
                lighthouse[metricName][record.client] = {{median_score: record[median_score]}};
            }});
        }});

        return Object.values(lighthouse);
        ''';

        SELECT
            STRING(DATE(date)) as date,
            app AS technology,
            rank,
            geo,
            GET_LIGHTHOUSE(ARRAY_AGG(STRUCT(
                client,
                median_lighthouse_score_accessibility,
                median_lighthouse_score_best_practices,
                median_lighthouse_score_performance,
                median_lighthouse_score_pwa,
                median_lighthouse_score_seo

            ))) AS lighthouse
        FROM
            `httparchive.core_web_vitals.technologies`
        WHERE date = '{date}'
        GROUP BY date, app, rank, geo
    """,
    "core_web_vitals": """
        CREATE TEMPORARY FUNCTION GET_VITALS(
            records ARRAY<STRUCT<
                client STRING,
                origins_with_good_fid INT64,
                origins_with_good_cls INT64,
                origins_with_good_lcp INT64,
                origins_with_good_fcp INT64,
                origins_with_good_ttfb INT64,
                origins_with_good_inp INT64,
                origins_with_any_fid INT64,
                origins_with_any_cls INT64,
                origins_with_any_lcp INT64,
                origins_with_any_fcp INT64,
                origins_with_any_ttfb INT64,
                origins_with_any_inp INT64,
                origins_with_good_cwv INT64,
                origins_eligible_for_cwv INT64
          >>
        ) RETURNS ARRAY<STRUCT<
            name STRING,
            desktop STRUCT<
                good_number INT64,
                tested INT64
        >,
        mobile STRUCT<
            good_number INT64,
            tested INT64
            >
        >> LANGUAGE js AS '''
        const METRIC_MAP = {{
            overall: ['origins_with_good_cwv', 'origins_eligible_for_cwv'],
            LCP: ['origins_with_good_lcp', 'origins_with_any_lcp'],
            CLS: ['origins_with_good_cls', 'origins_with_any_cls'],
            FID: ['origins_with_good_fid', 'origins_with_any_fid'],
            FCP: ['origins_with_good_fcp', 'origins_with_any_fcp'],
            TTFB: ['origins_with_good_ttfb', 'origins_with_any_ttfb'],
            INP: ['origins_with_good_inp', 'origins_with_any_inp']
        }};

        // Initialize the vitals map.
        const vitals = Object.fromEntries(Object.keys(METRIC_MAP).map(metricName => {{
            return [metricName, {{name: metricName}}];
        }}));

        // Populate each client record.
        records.forEach(record => {{
            Object.entries(METRIC_MAP).forEach(([metricName, [good_number, tested]]) => {{
                vitals[metricName][record.client] = {{good_number: record[good_number], tested: record[tested]}};
            }});
        }});

        return Object.values(vitals);
        ''';

        SELECT
            STRING(DATE(date)) as date,
            app AS technology,
            rank,
            geo,
            GET_VITALS(ARRAY_AGG(STRUCT(
                client,
                origins_with_good_fid,
                origins_with_good_cls,
                origins_with_good_lcp,
                origins_with_good_fcp,
                origins_with_good_ttfb,
                origins_with_good_inp,
                origins_with_any_fid,
                origins_with_any_cls,
                origins_with_any_lcp,
                origins_with_any_fcp,
                origins_with_any_ttfb,
                origins_with_any_inp,
                origins_with_good_cwv,
                origins_eligible_for_cwv
            ))) AS vitals
        FROM
            `httparchive.core_web_vitals.technologies`
        WHERE date = '{date}'
        GROUP BY date, app, rank, geo
    """,
    "technologies": """
        SELECT
            client,
            app AS technology,
            description,
            category,
            SPLIT(category, ",") AS category_obj,
            NULL AS similar_technologies,
            origins
        FROM
            `httparchive.core_web_vitals.technologies`
        JOIN
            `httparchive.core_web_vitals.technology_descriptions`
        ON
            app = technology
        WHERE date = '{date}' AND geo = 'ALL' AND rank = 'ALL'
        ORDER BY origins DESC
    """,
    "page_weight": """
        CREATE TEMPORARY FUNCTION GET_PAGE_WEIGHT(
            records ARRAY<STRUCT<
                client STRING,
                total INT64,
                js INT64,
                images INT64
            >>
        ) RETURNS ARRAY<STRUCT<
            name STRING,
            mobile STRUCT<
                median_bytes INT64
            >,
            desktop STRUCT<
                median_bytes INT64
            >
        >> LANGUAGE js AS '''
        const METRICS = ['total', 'js', 'images'];

        // Initialize the page weight map.
        const pageWeight = Object.fromEntries(METRICS.map(metricName => {{
        return [metricName, {{name: metricName}}];
        }}));

        // Populate each client record.
        records.forEach(record => {{
            METRICS.forEach(metricName => {{
                pageWeight[metricName][record.client] = {{median_bytes: record[metricName]}};
            }});
        }});

        return Object.values(pageWeight);
        ''';

        SELECT
            STRING(DATE(date)) as date,
            app AS technology,
            rank,
            geo,
            GET_PAGE_WEIGHT(ARRAY_AGG(STRUCT(
                client,
                median_bytes_total,
                median_bytes_js,
                median_bytes_image
            ))) AS pageWeight
        FROM
            `httparchive.core_web_vitals.technologies`
        WHERE date = '{date}'
        GROUP BY date, app, rank, geo
    """,
    "categories": """
        WITH categories AS (
            SELECT
                category,
                COUNT(DISTINCT root_page) AS origins
            FROM
                `httparchive.all.pages`,
                UNNEST(technologies) AS t,
                UNNEST(t.categories) AS category
            WHERE
                date = '{date}' AND
                client = 'mobile'
            GROUP BY
                category
            ),

            technologies AS (
            SELECT
                category,
                technology,
                COUNT(DISTINCT root_page) AS origins
            FROM
                `httparchive.all.pages`,
                UNNEST(technologies) AS t,
                UNNEST(t.categories) AS category
            WHERE
                date = '{date}' AND
                client = 'mobile'
            GROUP BY
                category,
                technology
            )

        SELECT
            category,
            categories.origins,
            ARRAY_AGG(technology ORDER BY technologies.origins DESC) AS technologies
        FROM
            categories
        JOIN
            technologies
        USING
            (category)
        GROUP BY
            category,
            categories.origins
        ORDER BY
            categories.origins DESC
  """
}
"""Mapping of query types to BigQuery SQL queries.
 The queries are formatted with the `date` parameter.
 Queries containing javascript UDFs require additional curly braces to escape the braces in the UDF.
"""
# editorconfig-checker-enable
