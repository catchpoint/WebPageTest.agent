# Copyright 2020 WebPageTest LLC.
# Copyright 2020 Google Inc.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Extract metadata from OpenType fonts."""

from fontTools.ttLib import TTFont
import functools
import logging


_NAME_ID_VERSION = 5
_NAME_ID_POSTSCRIPT_NAME = 6
_NAME_ID_LICENSE_URL = 14
_MAX_NAME_LEN = 64
_MAX_NAME_ID = 20
_MAX_NAMES = 20


def _safe_result_type(v):
    return type(v) in {bool, int, float, complex, str}


def _safe_map(m):
    return {k: v for k, v in m.items() if _safe_result_type(v)}


def _read_names(ttf, name_ids):
    names = {}
    try:
        # limit # of names we retain
        unicode_names = sorted(
            (n for n in ttf['name'].names
            if n.isUnicode() and n.nameID <= _MAX_NAME_ID),
            key=lambda n: n.nameID
        )[:_MAX_NAMES]
        # limit length of names we retain
        for name in unicode_names:
            try:
                names[name.nameID] = name.toUnicode()[:_MAX_NAME_LEN]
            except Exception:
                logging.exception('Error converting name to unicode')

    except Exception:
        logging.exception('Error reading font names')
    if not names:
        return None
    return names


def _read_os2(ttf):
    try:
        os2 = _safe_map(ttf['OS/2'].__dict__)
        os2['panose'] = _safe_map(ttf['OS/2'].panose.__dict__)
        return os2
    except Exception:
        logging.exception('Error reading font OS/2')
    return None


def _read_post(ttf):
    try:
        post = _safe_map(ttf['post'].__dict__)
        return post
    except Exception:
        logging.exception('Error reading font post')
    return None


def _read_fvar(ttf):
    if 'fvar' in ttf:
        try:
            return {
                a.axisTag: {
                    'min': a.minValue,
                    'default': a.defaultValue,
                    'max': a.maxValue
                }
                for a in ttf['fvar'].axes
            }
        except Exception:
            logging.exception('Error reading axes')
    return None


def _read_codepoint_glyph_counts(ttf):
    try:
        glyph_count = len(ttf.getGlyphOrder())
        unicode_cmaps = (t.cmap.keys() for t in ttf['cmap'].tables if t.isUnicode())
        unique_codepoints = functools.reduce(lambda acc, u: acc | u, unicode_cmaps, set())
        return {
            'num_cmap_codepoints': len(unique_codepoints),
            'num_glyphs': glyph_count
        }
    except Exception:
        logging.exception('Error reading codepoint and glyph count')
    return None


def read_metadata(font):
    ttf = TTFont(font, lazy=True)
    try:
        ttf.getGlyphNames()
    except Exception:
        logging.error('Not a vaild font: ' + request['url'])
        return None
    reader = ttf.reader

    metadata = {
        'table_sizes': {tag: reader.tables[tag].length 
                        for tag in sorted(reader.keys())},
        'names': _read_names(ttf, (_NAME_ID_VERSION,
            _NAME_ID_POSTSCRIPT_NAME, _NAME_ID_LICENSE_URL)),
        'OS2': _read_os2(ttf),
        'post': _read_post(ttf),
        'fvar': _read_fvar(ttf),
        'counts': _read_codepoint_glyph_counts(ttf),
    }
    ttf.close()

    return {k: v for k,v in metadata.items() if v is not None}


def main():
    import pprint
    import sys
    for filename in sys.argv[1:]:
        pp = pprint.PrettyPrinter()
        pp.pprint(read_metadata(filename))

if __name__ == "__main__":
    main()