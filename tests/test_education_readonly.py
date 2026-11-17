"""B134 handoff from MAB without running Minecraft code or making world claims."""
import copy
import json
import pytest
from app.education import import_b134_readback
from app.core import StudioError


def report():
    coords={'x':1,'y':64,'z':2}
    prefix='testforblock 1 64 2 minecraft:oak_stairs'
    commands=[prefix]+[prefix+f' ["weirdo_direction" = {n}]' for n in range(4)]
    rows=[{'command':c,'classification':k,
           'response':{'statusCode':0,'successCount':n,'statusMessage':'','transportError':False}}
          for c,k,n in zip(commands,['MATCH','NO_MATCH','MATCH','NO_MATCH','NO_MATCH'],[1,0,1,0,0])]
    return {'schema':'minecraftaibuilder-b134-live-readonly-diagnostic-v1',
            'readiness':'NOT_PREPARED','constructionAllowed':False,
            'authenticatedStairCertification':False,'operations':[],
            'independentImageReview':False,'walkabilityVerified':False,
            'coords':coords,'results':rows,
            'outcome':'UNIQUE_FOUR_STATE_RESPONSE_NEEDS_VISUAL_REVIEW',
            'provisionalStateValue':1}


def invoke(tmp_path,obj):
    dest=tmp_path/'B134_external.json'
    dest.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    return import_b134_readback(dest)


def test_b134_readonly_intake_keeps_provenance_unverified(tmp_path):
    output=invoke(tmp_path,report())
    assert output['outcome']=='UNIQUE_FOUR_STATE_RESPONSE_NEEDS_VISUAL_REVIEW'
    assert output['provisionalStateValue']==1
    assert output['externalEvidenceUnverified'] is True
    assert output['liveEducationConnectionFromStudio']=='NOT_TESTED'
    assert output['worldWrites']==0


@pytest.mark.parametrize('change',[
    lambda d:d.update(operations=[{'command':'fill'}]),
    lambda d:d.update(constructionAllowed=True),
    lambda d:d['results'][0].update(command='setblock 1 64 2 air'),
    lambda d:d.update(provisionalStateValue=3),
    lambda d:d.update(authenticatedStairCertification=True),
    lambda d:d.update(coords={'x':1,'y':513,'z':2}),
])
def test_b134_unsafe_or_self_contradictory_receipt_rejected(tmp_path,change):
    bad=report();change(bad)
    with pytest.raises(StudioError):invoke(tmp_path,bad)
