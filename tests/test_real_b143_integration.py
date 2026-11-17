"""Optional exact real engine test: MAB_B143_VERIFIED_ENGINE=/path/to/repo checkout.

No MAB source code is bundled in the app archive. A separate local checkout or
pinned source reconstructed and Git-blob-verified is required for this test.
"""
import hashlib
import os
from pathlib import Path
import pytest
from app import core


@pytest.mark.real_engine
def test_really_compiles_pinned_b143():
    repo = os.environ.get('MAB_B143_VERIFIED_ENGINE')
    if not repo:
        pytest.skip('Set MAB_B143_VERIFIED_ENGINE to an independently verified B143 source directory')
    root = Path(repo)
    source = (root/core.ENGINE_FILE).read_bytes()
    assert hashlib.sha256(source).hexdigest() == core.APPROVED_B143_SOURCE_SHA256
    assert core.compatibility_check(root)['geometrySha256'] == core.PINNED_SMOKE_GEOMETRY_SHA256
    design = {
        'schema':core.SCHEMA,'id':'real-integration-library','title':'Library',
        'brief':'Two-level concept with glass front and a doorway',
        'designOnly':True,'bounds':{'width':20,'height':16,'depth':20},
        'elements':[
            {'id':'walls','shape':'box','mode':'add','material':'stone',
             'origin':[2,1,2],'size':[16,9,16],'shell':1},
            {'id':'glass','shape':'box','mode':'add','material':'glass',
             'origin':[3,4,2],'size':[14,3,1]},
            {'id':'door','shape':'box','mode':'cut',
             'origin':[8,1,2],'size':[3,4,2]}
        ]}
    scene=core.run_bridge(root, design)
    assert scene['stats']['voxels']==917
    assert scene['geometrySha256']=='b706373c3f8e0a3598831d307141ad9405dd033b141118f6645430321fd24ea2'
    assert scene['BuildPlan']['operations']==[]
    assert scene['worldCoordinates'] is None and scene['worldContacts']==0


@pytest.mark.real_engine
def test_real_source_offline_archive_install_and_rollback(tmp_path):
    import io
    import zipfile
    root_text = os.environ.get('MAB_B143_VERIFIED_ENGINE')
    if not root_text:
        pytest.skip('Needs independently verified B143 source')
    source = (Path(root_text)/core.ENGINE_FILE).read_bytes()
    def archive(sha: str, data: bytes) -> bytes:
        out=io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('MinecraftAIBuilder-'+sha+'/', '')
            z.writestr('MinecraftAIBuilder-'+sha+'/'+core.ENGINE_FILE,data)
        return out.getvalue()
    manager=core.EngineManager(tmp_path/'studio')
    official_sha=core.BASELINE_SHA
    manager.install(official_sha,archive=archive(official_sha,source))
    assert manager.active_sha==official_sha
    # Synthetic commit labels are ONLY for offline transition testing; NOT GitHub commits.
    synthetic_sha='f'*40
    manager.install(synthetic_sha,archive=archive(synthetic_sha,source))
    assert manager.active_sha==synthetic_sha
    assert manager.rollback()==official_sha
    assert manager.active_sha==official_sha
    with pytest.raises(core.StudioError):
        manager.install('e'*40,archive=archive('e'*40,source+b'\n// unreviewed change'))
    assert manager.active_sha==official_sha
