"""Release identity describes validated scope, not universal HEC-RAS support."""
import os

VERSION = '0.2.0-dev'
BUILD_ID = os.environ.get('RASPLOT_BUILD_ID', 'working-tree-unreleased')
RELEASE = os.environ.get('RASPLOT_RELEASE', 'SOURCE')
PURPOSE = os.environ.get('RASPLOT_PURPOSE', 'Development')
SUPPORT = {
    'version': VERSION, 'build_id': BUILD_ID, 'release': RELEASE, 'purpose': PURPOSE,
    'scope': 'Validated single-reach and explicitly constructed multi-reach sources with a verified US/SI unit contract; engineering preflight required',
    'fixture_versions': {'North': '5.0.7', 'Muncie': '6.4.1', 'Industrial Park': 'validated Phase 6/7 fixture'},
    'unsupported': ['unvalidated topology or junction distances',
                    'structures without source-validated geometry or identity',
                    'unvalidated inline/gated structures', 'datum transformation',
                    'FDT/FIRM', 'DXF import'],
    'external_acceptance': ['AutoCAD/Civil 3D', 'release deployment environments'],
}
