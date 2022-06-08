# Copyright 2020 WebPageTest LLC.
# Copyright 2020 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Extract metadata from OpenType fonts."""

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables
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

def _read_cmap(ttf):
    try:
        encodings = [{ 'platform': t.platformID, 'encoding': t.platEncID } for t in ttf['cmap'].tables]
        codepoints = []

        cmap = ttf.getBestCmap()

        if cmap is not None:
            codepoints = [codepoint for codepoint in ttf.getBestCmap()]

        return {
            'encodings': encodings,
            'codepoints': codepoints,
        }
    except Exception:
        logging.exception('Error reading cmap data')
    return None

def _read_color(ttf):
    try:
        t = []

        # It is possible a single font uses multiple color
        # formats for wider OS and browser support.
        if 'COLR' in ttf and ttf['COLR'].version == 0:
            t.append('COLRv0')

        if 'COLR' in ttf and ttf['COLR'].version == 1:
            t.append('COLRv1')

        if 'SVG ' in ttf:
            t.append('SVG')

        if 'CBDT' in ttf:
            t.append('CBDT')

        if 'sbix' in ttf:
            t.append('sbix')

        numPalettes = 0
        numPaletteEntries = 0

        if 'CPAL' in ttf:
            numPaletteEntries = ttf['CPAL'].numPaletteEntries
            numPalettes = len(ttf['CPAL'].palettes)

        return {
            'formats': t,
            'numPalettes': numPalettes,
            'numPaletteEntries': numPaletteEntries
        }
    except Exception:
        logging.exception('Error reading color font data')
    return None

def _read_features(ttf):
    try:
        result = {}

        # This is loosely based on: https://github.com/fonttools/fonttools/blob/main/Snippets/layout-features.py
        for tag in ('GSUB', 'GPOS'):
            if not tag in ttf: continue
            table = ttf[tag].table

            if not tag in result:
                result[tag] = {}

            if not table.ScriptList or not table.FeatureList: continue
            featureRecords = table.FeatureList.FeatureRecord
            for script in table.ScriptList.ScriptRecord:
                if not script.Script: continue
                if not script.ScriptTag in result[tag]:
                    result[tag][script.ScriptTag] = {}

                languages = list(script.Script.LangSysRecord)

                if script.Script.DefaultLangSys:
                    defaultlangsys = otTables.LangSysRecord()
                    defaultlangsys.LangSysTag = "default"
                    defaultlangsys.LangSys = script.Script.DefaultLangSys
                    languages.insert(0, defaultlangsys)

                for langsys in languages:
                    if not langsys.LangSys: continue

                    if not langsys.LangSysTag in result[tag][script.ScriptTag]:
                        result[tag][script.ScriptTag][langsys.LangSysTag] = []

                    features = [featureRecords[index] for index in langsys.LangSys.FeatureIndex]

                    if langsys.LangSys.ReqFeatureIndex != 0xFFFF:
                        record = featureRecords[langsys.LangSys.ReqFeatureIndex]
                        requiredfeature = otTables.FeatureRecord()
                        requiredfeature.FeatureTag = 'required(%s)' % record.FeatureTag
                        requiredfeature.Feature = record.Feature
                        features.insert(0, requiredfeature)
                    for feature in features:
                        result[tag][script.ScriptTag][langsys.LangSysTag].append(feature.FeatureTag)

        return result
    except Exception:
        logging.exception('Error reading OpenType feature data')
    return None

def read_metadata(font):
    ttf = TTFont(font, fontNumber=0, lazy=True)
    try:
        ttf.getGlyphNames()
    except Exception:
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
        'cmap': _read_cmap(ttf),
        'color': _read_color(ttf),
        'features': _read_features(ttf),
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